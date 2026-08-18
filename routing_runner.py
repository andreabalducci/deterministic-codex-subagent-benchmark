#!/usr/bin/env python3
"""Execute one preregistered routing job in a fresh task workspace.

Raw Codex JSONL and stderr are private run artifacts.  The public result stores
only hashes, usage totals, runtime provenance, and sealed-evaluator outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

import harness
import routing_campaign
import routing_sequential
import routing_tasks


ROOT = Path(__file__).resolve().parent
RUN_ROOT = ROOT / "runs" / "routing"
RESULT_SCHEMA = ROOT / "schemas" / "routing-result.schema.json"
DEFAULT_RUNTIME_MANIFEST = ROOT / "protocols" / "routing-runtime-v1.json"
ROUTING_GENERATOR_IMAGE = "codex-routing-generator:0.147.0-dotnet10-node22-python3.12"
ROUTING_GENERATOR_DOCKERFILE = "docker/routing-generator.Dockerfile"
MAX_GENERATOR_OUTPUT_BYTES = 16 * 1024 * 1024
PROMPT = (
    "Read TASK.md and complete the assignment in this workspace. Work only in this "
    "directory and do not use the network. Do not spawn or delegate to other agents. "
    "Inspect the provided files, make the required artifact or code changes, and run "
    "any relevant visible checks. Finish with a concise summary of the result."
)
FAILURE_KINDS = {
    None, "generation-launcher", "generation-timeout", "generation-output-limit",
    "generation-transcript", "generation-unavailable", "generation-exit", "runtime-drift",
    "evaluation-launcher", "evaluation-failed",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def value_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def repository_provenance() -> dict[str, Any]:
    tracked = [
        ROOT / "routing_runner.py", ROOT / "routing_campaign.py", ROOT / "routing_preflight.py",
        ROOT / "routing_tasks.py",
        ROOT / "matrix.json", ROOT / "protocols" / "routing-v1.json",
        ROOT / "schemas" / "routing-result.schema.json",
        ROOT / "schemas" / "routing-preflight.schema.json",
        ROOT / "schemas" / "routing-task.schema.json",
        ROOT / ROUTING_GENERATOR_DOCKERFILE,
        ROOT / "docker" / "routing-evaluator.Dockerfile",
        ROOT / "docker" / "package.json", ROOT / "docker" / "package-lock.json",
    ]
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    return {
        "gitCommit": completed.stdout.strip() if completed.returncode == 0 else None,
        "files": {
            path.relative_to(ROOT).as_posix(): file_hash(path)
            for path in tracked if path.is_file()
        },
        "platform": {
            "system": platform.system(), "release": platform.release(),
            "machine": platform.machine(), "python": platform.python_version(),
        },
    }


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def parse_transcript(raw: bytes) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    valid = True
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            valid = False
            continue
        if not isinstance(event, dict):
            valid = False
            continue
        events.append(event)

    completed = any(event.get("type") == "turn.completed" for event in events)
    failed = any(event.get("type") in {"turn.failed", "error"} for event in events)
    usage_values: dict[str, int] = {
        "inputTokens": 0, "cachedInputTokens": 0, "outputTokens": 0,
        "reasoningOutputTokens": 0, "cacheWriteInputTokens": 0,
        "totalTokens": 0,
    }
    usage_seen = False
    aliases = {
        "input_tokens": "inputTokens", "cached_input_tokens": "cachedInputTokens",
        "output_tokens": "outputTokens", "reasoning_output_tokens": "reasoningOutputTokens",
        "reasoning_tokens": "reasoningOutputTokens",
        "cache_write_input_tokens": "cacheWriteInputTokens",
        "total_tokens": "totalTokens",
    }
    # Usage belongs to completed turns. Restricting the walk avoids double counting
    # cumulative thread summaries emitted in unrelated events.
    for event in events:
        if event.get("type") != "turn.completed":
            continue
        for node in _walk(event):
            if not isinstance(node, dict) or not any(key in node for key in aliases):
                continue
            for source, target in aliases.items():
                value = node.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage_values[target] += value
                    usage_seen = True
            break

    if usage_seen and usage_values["totalTokens"] == 0:
        usage_values["totalTokens"] = usage_values["inputTokens"] + usage_values["outputTokens"]

    def observed(keys: set[str]) -> str | None:
        found: list[str] = []
        for event in events:
            for node in _walk(event):
                if isinstance(node, dict):
                    for key in keys:
                        value = node.get(key)
                        if isinstance(value, str) and value.strip():
                            found.append(value.strip())
        return found[-1] if found else None

    return {
        "valid": valid and bool(events), "completed": completed, "failed": failed,
        "usage": usage_values if usage_seen else {key: None for key in usage_values},
        "observedModel": observed({"model", "model_name", "model_slug"}),
        "serviceTier": observed({"service_tier", "serviceTier"}),
    }


class Generator(Protocol):
    def generate(
        self, job: dict[str, Any], workspace: Path, prompt: str, transcript_path: Path
    ) -> dict[str, Any]: ...


def load_runtime_manifest(path: Path = DEFAULT_RUNTIME_MANIFEST) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schemaVersion", "recordKind", "codexVersion", "serviceTier", "multiAgent",
        "fastMode", "ephemeral", "ignoreUserConfig", "ignoreRules",
        "advertisedCapabilities", "observability",
    }
    if set(manifest) != expected or manifest["schemaVersion"] != 1 \
            or manifest["recordKind"] != "routing-runtime-controls":
        raise ValueError("unsupported routing runtime manifest")
    if manifest["serviceTier"] not in {"priority", "default"} \
            or manifest["multiAgent"] is not False \
            or manifest["fastMode"] is not (manifest["serviceTier"] == "priority") \
            or any(manifest[field] is not True for field in ("ephemeral", "ignoreUserConfig", "ignoreRules")):
        raise ValueError("routing runtime controls violate the worker experiment contract")
    capabilities = manifest["advertisedCapabilities"]
    if not isinstance(capabilities, dict) or set(capabilities) != {
        "reasoningEfforts", "serviceTier", "treatmentIds",
    } or capabilities["serviceTier"] != manifest["serviceTier"]:
        raise ValueError("runtime advertised capabilities are invalid")
    if manifest["observability"] != {
        "model": False, "serviceTier": False, "usage": True,
    }:
        raise ValueError("runtime telemetry contract is invalid")
    return manifest


class DockerCodexGenerator:
    def __init__(self, auth_file: Path, runtime_manifest: dict[str, Any], timeout: int = 1800):
        self.auth_file = harness.benchmark_auth_file(auth_file)
        self.runtime_manifest = runtime_manifest
        self.timeout = timeout

    @staticmethod
    def _container_arguments(workspace: Path, name: str, auth: Path) -> list[str]:
        return [
            "docker", "run", "--rm", "--interactive", "--name", name,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "512", "--memory", "2g", "--cpus", "4",
            *harness.docker_identity_arguments(),
            "--tmpfs", harness.docker_tmpfs("/tmp", "rw,noexec,nosuid,size=512m"),
            "--tmpfs", harness.docker_tmpfs("/codex-home", "rw,noexec,nosuid,size=32m"),
            "--env", "CODEX_HOME=/codex-home", "--env", "DOTNET_CLI_HOME=/tmp/dotnet-home",
            "--env", "NUGET_PACKAGES=/tmp/nuget", "--env", "npm_config_cache=/tmp/npm-cache",
            "--mount", f"type=bind,src={auth},dst=/codex-home/auth.json,readonly",
            "--mount", f"type=bind,src={workspace.resolve()},dst=/workspace",
            "--workdir", "/workspace",
        ]

    def generate(
        self, job: dict[str, Any], workspace: Path, prompt: str, transcript_path: Path
    ) -> dict[str, Any]:
        harness.ensure_image(ROUTING_GENERATOR_IMAGE, ROUTING_GENERATOR_DOCKERFILE)
        container_name = f"codex-routing-{job['runId']}"
        envelope = self._container_arguments(workspace, container_name, self.auth_file)
        version_attempt = harness.execute_captured(
            [*envelope, ROUTING_GENERATOR_IMAGE, "codex", "--version"],
            cwd=ROOT, environment=None, timeout=30, limit=64 * 1024,
        )
        version = version_attempt["stdout"].strip() if version_attempt["exitCode"] == 0 else None
        codex_arguments = harness.codex_exec_arguments(job["model"], job["reasoningEffort"])
        codex_arguments[-1:-1] = [
            "--config", f'service_tier="{self.runtime_manifest["serviceTier"]}"',
            "--config", f'features.fast_mode={str(self.runtime_manifest["fastMode"]).lower()}',
            "--config", "features.multi_agent=false",
        ]
        command = [
            *envelope, ROUTING_GENERATOR_IMAGE,
            *codex_arguments,
        ]

        def stop() -> None:
            subprocess.run(
                ["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False,
            )

        execution = harness.execute_captured(
            command, cwd=ROOT, environment=None, timeout=self.timeout,
            limit=MAX_GENERATOR_OUTPUT_BYTES, input_bytes=prompt.encode("utf-8"),
            timeout_callback=stop,
        )
        if execution["exitCode"] == 125:
            execution["launcherFailure"] = True
        harness.atomic_write(transcript_path, execution["stdout"].encode("utf-8"), mode=0o600)
        return {
            "execution": execution, "codexVersion": version,
            "generatorImage": harness.docker_image_info(ROUTING_GENERATOR_IMAGE),
            "backend": "docker", "isolation": "container-strong",
        }


def _generation_failure(outcome: dict[str, Any], transcript: dict[str, Any]) -> str | None:
    execution = outcome["execution"]
    if execution["launcherFailure"]:
        return "generation-launcher"
    if execution["timedOut"]:
        return "generation-timeout"
    if execution["outputLimitExceeded"]:
        return "generation-output-limit"
    if not transcript["valid"]:
        return "generation-transcript"
    if not transcript["completed"]:
        return "generation-unavailable"
    if execution["exitCode"] != 0 or transcript["failed"]:
        return "generation-exit"
    return None


def _schema_shape(result: dict[str, Any]) -> None:
    """Fast strict validator mirrored by the checked-in JSON Schema."""
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    required = set(schema["required"])
    if set(result) != required:
        raise ValueError(f"routing result fields differ: {sorted(set(result) ^ required)}")
    if result["schemaVersion"] != 2 or result["recordKind"] != "routing-result":
        raise ValueError("unsupported routing result identity")
    if result["status"] not in routing_campaign.ALLOWED_STATUSES:
        raise ValueError("invalid routing result status")
    if result["failureKind"] not in FAILURE_KINDS:
        raise ValueError("invalid routing result failure kind")
    if (result["status"] == "PASS") != (result["failureKind"] is None):
        raise ValueError("status and failure kind disagree")
    hashes = [
        result["protocolHash"], result["planHash"], result["runtimeManifestHash"],
        result["preflightReportHash"], result["capabilityDigest"],
        result["fixtureManifestHash"], result["promptHash"], result["transcriptHash"],
        result["candidateHash"], result["generation"]["attemptHash"],
        result["evaluation"]["reportHash"], *result["provenance"]["files"].values(),
    ]
    if any(value is not None and (
        not isinstance(value, str) or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ) for value in hashes):
        raise ValueError("routing result contains a non-canonical hash")
    for token in result["usage"].values():
        if token is not None and (
            not isinstance(token, int) or isinstance(token, bool) or token < 0
        ):
            raise ValueError("routing result contains invalid token usage")
    for duration in (
        result["generationDurationSeconds"], result["evaluationDurationSeconds"],
        result["totalDurationSeconds"],
    ):
        if duration is not None and (
            not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration < 0
        ):
            raise ValueError("routing result contains an invalid duration")
    if set(result["provenance"]) != {"gitCommit", "files", "platform"} \
            or set(result["provenance"]["platform"]) != {
                "system", "release", "machine", "python",
            }:
        raise ValueError("routing result provenance is not strict")


def run_job(
    protocol: dict[str, Any], plan: dict[str, Any], run_id: str, generator: Generator,
    *, machine_id: str, run_root: Path = RUN_ROOT, evaluation_backend: str = "docker",
    evaluator=routing_tasks.evaluate_artifact,
    runtime_manifest: dict[str, Any] | None = None,
    preflight_report: dict[str, Any],
    sequential_state: dict[str, Any] | None = None,
    sequential_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import routing_preflight  # lazy import: preflight uses runner runtime/image helpers

    runtime_manifest = runtime_manifest or load_runtime_manifest()
    routing_campaign.validate_plan(plan, protocol)
    if value_hash(runtime_manifest) != protocol["runtimeManifestHash"]:
        raise ValueError("runtime manifest does not match the preregistered protocol hash")
    capabilities = runtime_manifest["advertisedCapabilities"]
    if set(capabilities["treatmentIds"]) != {item["id"] for item in protocol["matrix"]} \
            or not {item["reasoningEffort"] for item in protocol["matrix"]}.issubset(
                set(capabilities["reasoningEfforts"])
            ):
        raise ValueError("runtime capabilities do not cover the protocol matrix")
    matches = [job for job in plan["jobs"] if job["runId"] == run_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or duplicate run ID: {run_id}")
    job = matches[0]
    if machine_id != job["machineId"]:
        raise ValueError(
            f"job {run_id} is assigned to {job['machineId']}, not declared machine {machine_id}"
        )
    try:
        routing_preflight.validate_report(
            preflight_report, protocol, runtime_manifest, expected_machine_id=machine_id
        )
    except routing_preflight.PreflightError as error:
        raise ValueError(f"assigned-machine preflight is invalid: {error}") from error
    binding = next(
        item for item in plan["preflightBindings"] if item["machineId"] == machine_id
    )
    if routing_campaign.value_hash(preflight_report) != binding["reportHash"] \
            or preflight_report["capabilityDigest"] != binding["capabilityDigest"]:
        raise ValueError("assigned-machine preflight does not match the frozen plan binding")
    authorized_run_ids: set[str] | None = None
    if sequential_state is not None or sequential_manifest is not None:
        if sequential_state is None or sequential_manifest is None:
            raise ValueError("sequential state and manifest must be supplied together")
        routing_sequential.validate_manifest(sequential_manifest, plan, protocol)
        routing_sequential.validate_state(sequential_state, sequential_manifest, plan, protocol)
        prior_results: dict[str, dict[str, Any]] = {}
        for result_file in (run_root / "results").glob("*.json"):
            try:
                prior = routing_campaign.load_json(result_file)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(prior, dict) and isinstance(prior.get("runId"), str):
                prior_results[prior["runId"]] = prior
        routing_sequential.replay_state(
            sequential_state, sequential_manifest, plan, protocol, prior_results
        )
        authorized_run_ids = {
            authorized_run_id
            for authorization in sequential_state["authorized"]
            for authorized_run_id in authorization["runIds"]
        }
        if run_id not in authorized_run_ids:
            raise ValueError(f"run {run_id} is not authorized by the sequential state")
    workspace = harness.ensure_strict_descendant(run_root / "workspaces" / run_id, run_root / "workspaces")
    transcript_path = harness.ensure_strict_descendant(
        run_root / "transcripts" / f"{run_id}.jsonl", run_root / "transcripts"
    )
    attempt_path = harness.ensure_strict_descendant(
        run_root / "transcripts" / f"{run_id}.meta.json", run_root / "transcripts"
    )
    result_path = harness.ensure_strict_descendant(
        run_root / "results" / f"{run_id}.json", run_root / "results"
    )
    if result_path.exists() or transcript_path.exists() or attempt_path.exists() or workspace.exists():
        raise FileExistsError(f"run {run_id} already has artifacts; never overwrite a sample")
    job_index = plan["jobs"].index(job)
    predecessors = [
        item for item in plan["jobs"][:job_index] if item["machineId"] == machine_id
        and (authorized_run_ids is None or item["runId"] in authorized_run_ids)
    ]
    for predecessor in predecessors:
        predecessor_path = run_root / "results" / f"{predecessor['runId']}.json"
        if not predecessor_path.exists():
            raise ValueError(
                f"physical order violation: predecessor {predecessor['runId']} is missing"
            )
        previous = json.loads(predecessor_path.read_text(encoding="utf-8"))
        routing_campaign.validate_results([previous], plan, protocol)
        if previous["status"] == "INFRA_FAILURE":
            raise ValueError(
                f"campaign is paused after infrastructure failure {predecessor['runId']}"
            )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    routing_tasks.materialize(job["fixtureId"], workspace)
    started = time.monotonic()
    generation = generator.generate(job, workspace, PROMPT, transcript_path)
    generation_duration = float(generation["execution"]["durationSeconds"])
    execution = generation["execution"]
    attempt = {
        "exitCode": execution["exitCode"], "durationSeconds": execution["durationSeconds"],
        "stderr": execution.get("stderr", ""), "timedOut": execution["timedOut"],
        "launcherFailure": execution["launcherFailure"],
        "outputLimitExceeded": execution["outputLimitExceeded"],
    }
    harness.save_json(attempt_path, attempt, private=True, replace=False)
    raw_transcript = transcript_path.read_bytes()
    transcript = parse_transcript(raw_transcript)
    failure = _generation_failure(generation, transcript)
    if failure is None and (
        generation.get("codexVersion") != runtime_manifest["codexVersion"]
        or (
            transcript["observedModel"] is not None
            and transcript["observedModel"] != job["model"]
        )
        or (
            transcript["serviceTier"] is not None
            and transcript["serviceTier"] != runtime_manifest["serviceTier"]
        )
    ):
        failure = "runtime-drift"
    evaluation_report: dict[str, Any] | None = None
    evaluation_duration: float | None = None
    candidate_digest: str | None = None
    if failure is None:
        evaluation_started = time.monotonic()
        try:
            evaluation_report = evaluator(
                job["fixtureId"], workspace, backend=evaluation_backend
            )
        except Exception:
            failure = "evaluation-launcher"
        evaluation_duration = round(time.monotonic() - evaluation_started, 6)
        if evaluation_report is not None:
            candidate_digest = evaluation_report.get("candidateHash")
            if evaluation_report.get("status") != "PASS":
                failure = "evaluation-failed"

    task = routing_tasks.task_by_id(job["fixtureId"])
    spec = routing_tasks.load_template(task)
    status = (
        "PASS" if failure is None else
        "INFRA_FAILURE" if failure in {
            "generation-launcher", "generation-timeout", "generation-output-limit",
            "generation-transcript", "generation-unavailable", "runtime-drift",
            "evaluation-launcher",
        } else "CANDIDATE_FAILURE"
    )
    evaluator_image = (
        harness.docker_image_info(routing_tasks.ROUTING_EVALUATOR_IMAGE)
        if evaluation_backend == "docker" and evaluation_report is not None else None
    )
    result = {
        "schemaVersion": 2, "recordKind": "routing-result",
        "protocolHash": routing_campaign.value_hash(protocol),
        "planHash": routing_campaign.value_hash(plan),
        "runtimeManifestHash": value_hash(runtime_manifest),
        "preflightReportHash": binding["reportHash"],
        "capabilityDigest": binding["capabilityDigest"],
        "runId": run_id, "familyId": job["familyId"], "fixtureId": job["fixtureId"],
        "replicate": job["replicate"], "machineId": job["machineId"],
        "orderPosition": job["orderPosition"], "treatmentId": job["treatmentId"],
        "model": job["model"], "reasoningEffort": job["reasoningEffort"],
        "status": status, "failureKind": failure,
        "fixtureManifestHash": task["manifestHash"],
        "promptHash": sha256_bytes(PROMPT.encode("utf-8")),
        "candidateHash": candidate_digest,
        "transcriptHash": sha256_bytes(raw_transcript),
        "generation": {
            "backend": generation["backend"], "isolation": generation["isolation"],
            "generatorImage": generation.get("generatorImage"),
            "codexVersion": generation.get("codexVersion"),
            "attemptHash": value_hash(attempt),
            "requestedModel": job["model"],
            "requestedReasoningEffort": job["reasoningEffort"],
            "requestedServiceTier": runtime_manifest["serviceTier"],
            "fastMode": runtime_manifest["fastMode"],
            "multiAgent": runtime_manifest["multiAgent"],
            "observedModel": transcript["observedModel"],
            "serviceTier": transcript["serviceTier"],
            "runtimeVerification": (
                "telemetry-confirmed"
                if transcript["observedModel"] is not None
                and transcript["serviceTier"] is not None
                else "cli-request-and-success"
            ),
        },
        "usage": transcript["usage"],
        "evaluation": {
            "backend": evaluation_backend, "evaluatorProfile": spec.get("evaluatorProfile"),
            "evaluatorImage": evaluator_image,
            "reportHash": value_hash(evaluation_report) if evaluation_report is not None else None,
        },
        "generationDurationSeconds": round(generation_duration, 6),
        "evaluationDurationSeconds": evaluation_duration,
        "totalDurationSeconds": round(time.monotonic() - started, 6),
        "provenance": repository_provenance(),
    }
    _schema_shape(result)
    routing_campaign.validate_results([result], plan, protocol)
    harness.save_json(result_path, result, replace=False)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=routing_campaign.DEFAULT_PROTOCOL)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    parser.add_argument("--runtime-manifest", type=Path, default=DEFAULT_RUNTIME_MANIFEST)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--sequential-state", type=Path)
    parser.add_argument("--sequential-manifest", type=Path)
    parser.add_argument("--agent-timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    protocol = routing_campaign.validate_protocol(routing_campaign.load_json(args.protocol))
    plan = routing_campaign.load_json(args.plan)
    runtime_manifest = load_runtime_manifest(args.runtime_manifest)
    sequential_state = None
    sequential_manifest = None
    if args.sequential_state is not None or args.sequential_manifest is not None:
        if args.sequential_state is None or args.sequential_manifest is None:
            raise ValueError("--sequential-state and --sequential-manifest must be supplied together")
        sequential_state = routing_campaign.load_json(args.sequential_state)
        sequential_manifest = routing_campaign.load_json(args.sequential_manifest)
    result = run_job(
        protocol, plan, args.run_id,
        DockerCodexGenerator(args.auth_file, runtime_manifest, timeout=args.agent_timeout),
        machine_id=args.machine_id, run_root=args.run_root.resolve(),
        runtime_manifest=runtime_manifest,
        preflight_report=routing_campaign.load_json(args.preflight),
        sequential_state=sequential_state,
        sequential_manifest=sequential_manifest,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] != "INFRA_FAILURE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
