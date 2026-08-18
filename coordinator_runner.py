#!/usr/bin/env python3
"""Execute one live coordinator-treatment job with a frozen worker policy.

The raw JSONL transcript and timestamp sidecar are private evidence.  The public
result contains normalized delegation records and aggregate coordination metrics.
Unlike routing_runner.py, this runner deliberately enables multi-agent support.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import coordinator_campaign
import harness
import routing_runner
import routing_tasks

ROOT = Path(__file__).resolve().parent
RUN_ROOT = ROOT / "runs" / "coordinator"
GENERATOR_IMAGE = routing_runner.ROUTING_GENERATOR_IMAGE
GENERATOR_DOCKERFILE = routing_runner.ROUTING_GENERATOR_DOCKERFILE
MAX_OUTPUT = 16 * 1024 * 1024


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def value_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def coordinator_prompt(protocol: dict[str, Any]) -> str:
    worker = protocol["workerPolicy"]
    prompts = "\n\n".join(
        f"WORKER SLOT {index} — delegate this exact message verbatim:\n{prompt}"
        for index, prompt in enumerate(worker["prompts"], 1)
    )
    return (
        "Read TASK.md, WORK_ITEM.md, and repo.snapshot.json. You are the live coordinator, "
        "not a leaf planner. Do not use the network for task work. Spawn exactly three fresh "
        "leaf workers concurrently, using the "
        f"fixed worker model {worker['model']} and reasoning effort {worker['reasoningEffort']}; "
        "set fork_turns to none for every spawn. "
        "Each worker must use its assigned exact message below and must not delegate. Do not "
        "start integration until all three return. Reconcile ownership conflicts, inspect every "
        "worker edit, and complete all four JSON files under integration/. Do not modify TASK.md, "
        "WORK_ITEM.md, or repo.snapshot.json.\n\n"
        f"{prompts}\n\n"
        "Your final response must summarize integration acceptance and any coordinator "
        "interventions or conflicts. The runtime trace, not your prose, is the authority for "
        "whether delegation occurred."
    )


class Generator(Protocol):
    def generate(self, job: dict[str, Any], workspace: Path, prompt: str, transcript_path: Path) -> dict[str, Any]: ...


def _find(node: Any, keys: set[str]) -> str | None:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value:
                return value
        for child in node.values():
            found = _find(child, keys)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = _find(child, keys)
            if found is not None:
                return found
    return None


def _arguments(node: dict[str, Any]) -> dict[str, Any]:
    value = node.get("arguments", node.get("args", {}))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _tool_record(node: Any) -> tuple[str, dict[str, Any], str] | None:
    if isinstance(node, dict):
        name = next((node[key] for key in ("tool", "tool_name", "function_name", "name")
                     if isinstance(node.get(key), str)), "")
        leaf = name.rsplit(".", 1)[-1].rsplit("__", 1)[-1]
        if leaf in {"spawn_agent", "wait_agent", "followup_task", "send_message", "interrupt_agent"}:
            agent_id = _find(node, {"agent_id", "agentId"}) or ""
            return leaf, _arguments(node), agent_id
        for child in node.values():
            found = _tool_record(child)
            if found is not None: return found
    elif isinstance(node, list):
        for child in node:
            found = _tool_record(child)
            if found is not None: return found
    return None


def parse_trace(raw: bytes, event_times_ms: list[int], protocol: dict[str, Any]) -> dict[str, Any]:
    """Normalize explicit collaboration events; never infer delegation from prose."""
    events: list[tuple[dict[str, Any], int]] = []
    for index, line in enumerate(raw.decode("utf-8", errors="replace").splitlines()):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            timestamp = event_times_ms[index] if index < len(event_times_ms) else 0
            events.append((event, timestamp))

    workers: dict[str, dict[str, Any]] = {}
    interventions = 0
    conflicts_observed = 0
    conflicts_resolved = 0
    for event, timestamp in events:
        event_type = event.get("type")
        if event_type == "collaboration.spawn":
            args, agent_id = event, str(event.get("agentId", ""))
            tool = "spawn_agent"
        elif event_type == "collaboration.worker.completed":
            agent_id = str(event.get("agentId", "")); args = event; tool = "worker_completed"
        elif event_type == "collaboration.intervention":
            interventions += 1; continue
        elif event_type == "collaboration.conflict":
            conflicts_observed += 1
            if event.get("resolved") is True: conflicts_resolved += 1
            continue
        else:
            record = _tool_record(event)
            if record is None: continue
            tool, args, observed_agent_id = record
            agent_id = str(args.get("agent_id", args.get("agentId", observed_agent_id)))
        if tool == "spawn_agent":
            prompt = args.get("message", args.get("prompt", ""))
            agent_id = agent_id or str(args.get("result_agent_id", ""))
            if not agent_id:
                agent_id = f"unresolved-{len(workers) + 1}"
            workers[agent_id] = {
                "agentId": agent_id, "prompt": prompt,
                "model": args.get("model"),
                "reasoningEffort": args.get("reasoning_effort", args.get("reasoningEffort")),
                "forkTurns": args.get("fork_turns", args.get("forkTurns")),
                "startedAtMs": timestamp, "completedAtMs": None, "status": "incomplete",
                "nestedDelegations": args.get("nestedDelegations"),
                "artifactHash": args.get("artifactHash"),
            }
        elif tool in {"worker_completed", "wait_agent", "wait_threads"}:
            # A wait call is not evidence that every worker completed.  Normalize
            # only an explicitly identified target and otherwise fail closed.
            targets = [agent_id] if agent_id else []
            for target in targets:
                if target in workers and workers[target]["completedAtMs"] is None:
                    workers[target]["completedAtMs"] = timestamp
                    workers[target]["status"] = "completed" if args.get("failed") is not True else "failed"
                    artifact = args.get("artifactHash")
                    if isinstance(artifact, str): workers[target]["artifactHash"] = artifact
        elif tool in {"followup_task", "send_message", "interrupt_agent"}:
            interventions += 1

    policy = protocol["workerPolicy"]
    expected_hashes = [sha256_bytes(prompt.encode("utf-8")) for prompt in policy["prompts"]]
    normalized = []
    for slot, worker in enumerate(workers.values(), 1):
        prompt_hash = sha256_bytes(str(worker.pop("prompt", "")).encode("utf-8"))
        normalized.append({"workerSlot": slot, "promptHash": prompt_hash, **worker})
    durations = [
        (item["completedAtMs"] - item["startedAtMs"]) / 1000
        for item in normalized if item["completedAtMs"] is not None
        and item["completedAtMs"] >= item["startedAtMs"]
    ]
    starts = [item["startedAtMs"] for item in normalized]
    ends = [item["completedAtMs"] for item in normalized if item["completedAtMs"] is not None]
    critical = (max(ends) - min(starts)) / 1000 if starts and len(ends) == len(starts) else None
    total = sum(durations)
    utilization = min(1.0, total / (critical * policy["concurrency"])) \
        if critical is not None and critical > 0 else None
    concurrency_compliant = bool(starts and ends and max(starts) <= min(ends))
    prompt_set = [item["promptHash"] for item in normalized]
    compliant = (
        len(normalized) == policy["workerCount"]
        and sorted(prompt_set) == sorted(expected_hashes)
        and all(item["model"] == policy["model"] for item in normalized)
        and all(item["reasoningEffort"] == policy["reasoningEffort"] for item in normalized)
        and all(item["forkTurns"] == "none" for item in normalized)
        and all(item["status"] == "completed" for item in normalized)
        and all(item["nestedDelegations"] == 0 for item in normalized)
        and critical is not None and critical > 0 and concurrency_compliant
    )
    return {
        "delegations": normalized,
        "metrics": {
            "traceCompliance": compliant, "delegatedCount": len(normalized),
            "completedCount": sum(item["status"] == "completed" for item in normalized),
            "concurrencyCompliance": concurrency_compliant,
            "interventionCount": interventions, "conflictsObserved": conflicts_observed,
            "conflictsResolved": conflicts_resolved,
            "unresolvedConflicts": conflicts_observed - conflicts_resolved,
            "criticalPathSeconds": critical, "totalWorkerSeconds": total,
            "utilization": utilization,
        },
    }


def provenance() -> dict[str, Any]:
    paths = [
        ROOT / "coordinator_campaign.py", ROOT / "coordinator_runner.py",
        ROOT / "protocols/coordinator-v1.json",
        ROOT / "schemas/coordinator-protocol.schema.json",
        ROOT / "schemas/coordinator-plan.schema.json",
        ROOT / "schemas/coordinator-result.schema.json",
    ]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return {
        "gitCommit": commit.stdout.strip() if commit.returncode == 0 else None,
        "files": {path.relative_to(ROOT).as_posix(): sha256_bytes(path.read_bytes()) for path in paths},
        "platform": {"system": platform.system(), "release": platform.release(),
                     "machine": platform.machine(), "python": platform.python_version()},
    }


RESULT_KEYS = {
    "schemaVersion", "recordKind", "protocolHash", "planHash", "runId", "fixtureId",
    "replicate", "machineId", "orderPosition", "coordinatorTreatmentId",
    "coordinatorModel", "coordinatorReasoningEffort", "workerPolicyHash", "status",
    "failureKind", "promptHash", "transcriptHash", "generationDurationSeconds", "usage",
    "runtime", "delegations", "coordination", "integration", "provenance",
}


def validate_result(result: Any, protocol: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != RESULT_KEYS:
        raise ValueError("coordinator result has non-canonical fields")
    if result["schemaVersion"] != 1 or result["recordKind"] != "coordinator-result" \
            or result["protocolHash"] != coordinator_campaign.value_hash(protocol) \
            or result["planHash"] != coordinator_campaign.value_hash(plan):
        raise ValueError("coordinator result identity or frozen hashes differ")
    matches = [job for job in plan["jobs"] if job["runId"] == result["runId"]]
    if len(matches) != 1: raise ValueError("coordinator result does not belong to plan")
    job = matches[0]
    mapping = {
        "fixtureId": "fixtureId", "replicate": "replicate", "machineId": "machineId",
        "orderPosition": "orderPosition", "coordinatorTreatmentId": "coordinatorTreatmentId",
        "coordinatorModel": "coordinatorModel",
        "coordinatorReasoningEffort": "coordinatorReasoningEffort",
        "workerPolicyHash": "workerPolicyHash",
    }
    if any(result[target] != job[source] for target, source in mapping.items()):
        raise ValueError("coordinator result job fields drifted")
    if result["status"] not in {"PASS", "CANDIDATE_FAILURE", "INFRA_FAILURE"} \
            or (result["status"] == "PASS") != (result["failureKind"] is None):
        raise ValueError("coordinator result status disagrees with failure")
    hashes = [result[key] for key in ("protocolHash", "planHash", "workerPolicyHash", "promptHash", "transcriptHash")]
    for item in result["delegations"]:
        hashes.append(item["promptHash"])
        if item["artifactHash"] is not None: hashes.append(item["artifactHash"])
    hashes.extend(result["provenance"]["files"].values())
    if any(not isinstance(value, str) or len(value) != 64 \
           or any(character not in "0123456789abcdef" for character in value) for value in hashes):
        raise ValueError("coordinator result contains an invalid hash")
    if result["status"] == "PASS" and (not result["coordination"]["traceCompliance"]
                                       or not result["integration"]["accepted"]):
        raise ValueError("passing coordinator result lacks trace or integration acceptance")
    return result


def run_job(protocol: dict[str, Any], plan: dict[str, Any], run_id: str, generator: Generator,
            *, machine_id: str, run_root: Path = RUN_ROOT, evaluator=routing_tasks.evaluate_artifact,
            evaluation_backend: str = "docker") -> dict[str, Any]:
    coordinator_campaign.validate_protocol(protocol, catalog=routing_tasks.load_catalog())
    coordinator_campaign.validate_plan(plan, protocol)
    matches = [job for job in plan["jobs"] if job["runId"] == run_id]
    if len(matches) != 1: raise ValueError(f"unknown run ID: {run_id}")
    job = matches[0]
    if job["machineId"] != machine_id: raise ValueError("machine assignment mismatch")
    workspace = run_root / "workspaces" / run_id
    transcript_path = run_root / "transcripts" / f"{run_id}.jsonl"
    result_path = run_root / "results" / f"{run_id}.json"
    timestamp_path = run_root / "transcripts" / f"{run_id}.timestamps.json"
    if any(path.exists() for path in (workspace, transcript_path, result_path, timestamp_path)):
        raise FileExistsError("coordinator samples are immutable and cannot be overwritten")
    predecessors = [item for item in plan["jobs"]
                    if item["machineId"] == machine_id
                    and item["orderPosition"] < job["orderPosition"]]
    for predecessor in predecessors:
        predecessor_path = run_root / "results" / f"{predecessor['runId']}.json"
        if not predecessor_path.exists():
            raise ValueError(f"physical order violation: {predecessor['runId']} is missing")
        previous = json.loads(predecessor_path.read_text(encoding="utf-8"))
        validate_result(previous, protocol, plan)
        if previous["status"] == "INFRA_FAILURE":
            raise ValueError(f"campaign paused after infrastructure failure {predecessor['runId']}")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    routing_tasks.materialize(job["fixtureId"], workspace)
    prompt = coordinator_prompt(protocol)
    outcome = generator.generate(job, workspace, prompt, transcript_path)
    raw = transcript_path.read_bytes()
    times = outcome.get("eventTimesMs", [])
    harness.save_json(timestamp_path, times, private=True, replace=False)
    parsed = routing_runner.parse_transcript(raw)
    trace = parse_trace(raw, times, protocol)
    failure: str | None = None
    execution = outcome["execution"]
    if execution.get("launcherFailure") or execution.get("timedOut") \
            or execution.get("outputLimitExceeded") or execution.get("exitCode") != 0 \
            or not parsed["completed"]:
        failure = "generation-infrastructure"
    envelope = protocol["executionEnvelope"]
    observed_model = parsed["observedModel"]
    observed_tier = parsed["serviceTier"]
    if failure is None and (outcome.get("codexVersion") != envelope["codexVersion"]
                            or (observed_model is not None and observed_model != job["coordinatorModel"])
                            or (observed_tier is not None and observed_tier != envelope["serviceTier"])):
        failure = "runtime-drift"
    report = None
    if failure is None:
        report = evaluator(job["fixtureId"], workspace, backend=evaluation_backend)
        if not trace["metrics"]["traceCompliance"] \
                or trace["metrics"]["unresolvedConflicts"] > protocol["acceptance"]["maximumUnresolvedConflicts"]:
            failure = "trace-contract"
        elif report.get("status") != "PASS":
            failure = "integration-rejected"
    status = "PASS" if failure is None else (
        "INFRA_FAILURE" if failure in {"generation-infrastructure", "runtime-drift"}
        else "CANDIDATE_FAILURE"
    )
    result = {
        "schemaVersion": 1, "recordKind": "coordinator-result",
        "protocolHash": coordinator_campaign.value_hash(protocol),
        "planHash": coordinator_campaign.value_hash(plan),
        "runId": run_id, "fixtureId": job["fixtureId"], "replicate": job["replicate"],
        "machineId": machine_id, "orderPosition": job["orderPosition"],
        "coordinatorTreatmentId": job["coordinatorTreatmentId"],
        "coordinatorModel": job["coordinatorModel"],
        "coordinatorReasoningEffort": job["coordinatorReasoningEffort"],
        "workerPolicyHash": job["workerPolicyHash"], "status": status,
        "failureKind": failure, "promptHash": sha256_bytes(prompt.encode("utf-8")),
        "transcriptHash": sha256_bytes(raw),
        "generationDurationSeconds": float(execution.get("durationSeconds", 0)),
        "usage": parsed["usage"],
        "runtime": {"codexVersion": outcome.get("codexVersion"),
                    "observedCoordinatorModel": observed_model, "serviceTier": observed_tier,
                    "fastMode": envelope["fastMode"], "multiAgent": True},
        "delegations": trace["delegations"], "coordination": trace["metrics"],
        "integration": {"accepted": bool(report and report.get("status") == "PASS"),
                        "candidateHash": report.get("candidateHash") if report else None,
                        "reportHash": value_hash(report) if report else None},
        "provenance": provenance(),
    }
    validate_result(result, protocol, plan)
    harness.save_json(result_path, result, replace=False)
    return result


class DockerCoordinatorGenerator:
    def __init__(self, auth_file: Path, protocol: dict[str, Any], timeout: int = 1800):
        self.auth_file = harness.benchmark_auth_file(auth_file)
        self.protocol = protocol
        self.timeout = timeout

    def generate(self, job: dict[str, Any], workspace: Path, prompt: str, transcript_path: Path) -> dict[str, Any]:
        harness.ensure_image(GENERATOR_IMAGE, GENERATOR_DOCKERFILE)
        name = f"codex-coordinator-{job['runId']}"
        args = routing_runner.DockerCodexGenerator._container_arguments(workspace, name, self.auth_file)
        codex = harness.codex_exec_arguments(job["coordinatorModel"], job["coordinatorReasoningEffort"])
        envelope = self.protocol["executionEnvelope"]
        codex[-1:-1] = ["--config", f'service_tier="{envelope["serviceTier"]}"',
                        "--config", f'features.fast_mode={str(envelope["fastMode"]).lower()}',
                        "--config", "features.multi_agent=true"]
        command = [*args, GENERATOR_IMAGE, *codex]
        version_attempt = harness.execute_captured(
            [*args, GENERATOR_IMAGE, "codex", "--version"], cwd=ROOT,
            environment=None, timeout=30, limit=64 * 1024,
        )
        version = version_attempt["stdout"].strip() if version_attempt["exitCode"] == 0 else None
        started = time.monotonic()
        try:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            harness.atomic_write(transcript_path, b"", mode=0o600)
            return {"execution": {"exitCode": None, "durationSeconds": 0.0,
                    "timedOut": False, "launcherFailure": True,
                    "outputLimitExceeded": False, "stderr": str(error)},
                    "eventTimesMs": [], "codexVersion": version}
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write(prompt.encode("utf-8")); process.stdin.close()
        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        event_times: list[int] = []
        exceeded = threading.Event()

        def read_stdout() -> None:
            size = 0
            for line in iter(process.stdout.readline, b""):
                event_times.append(round((time.monotonic() - started) * 1000))
                if size + len(line) <= MAX_OUTPUT:
                    stdout_parts.append(line); size += len(line)
                else:
                    exceeded.set()

        def read_stderr() -> None:
            size = 0
            for chunk in iter(lambda: process.stderr.read(65536), b""):
                if size + len(chunk) <= MAX_OUTPUT:
                    stderr_parts.append(chunk); size += len(chunk)
                else:
                    exceeded.set()

        readers = [threading.Thread(target=read_stdout), threading.Thread(target=read_stderr)]
        for reader in readers: reader.start()
        timed_out = False
        try:
            exit_code = process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
            process.kill(); exit_code = process.wait()
        for reader in readers: reader.join(timeout=5)
        raw = b"".join(stdout_parts)
        harness.atomic_write(transcript_path, raw, mode=0o600)
        execution = {"exitCode": exit_code, "durationSeconds": round(time.monotonic() - started, 6),
                     "timedOut": timed_out, "launcherFailure": exit_code == 125,
                     "outputLimitExceeded": exceeded.is_set(),
                     "stderr": b"".join(stderr_parts).decode("utf-8", errors="replace")}
        return {"execution": execution, "eventTimesMs": event_times, "codexVersion": version}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=coordinator_campaign.DEFAULT_PROTOCOL)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--run-id", required=True); parser.add_argument("--machine-id", required=True)
    parser.add_argument("--auth-file", type=Path, required=True); parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    args = parser.parse_args(argv)
    protocol = coordinator_campaign.validate_protocol(coordinator_campaign.load_json(args.protocol), catalog=routing_tasks.load_catalog())
    plan = coordinator_campaign.validate_plan(coordinator_campaign.load_json(args.plan), protocol)
    result = run_job(protocol, plan, args.run_id, DockerCoordinatorGenerator(args.auth_file, protocol),
                     machine_id=args.machine_id, run_root=args.run_root.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] != "INFRA_FAILURE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
