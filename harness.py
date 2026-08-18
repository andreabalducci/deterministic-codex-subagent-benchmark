#!/usr/bin/env python3
"""Deterministic evaluator and isolated Codex generation runner."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import platform
import secrets
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
FIXTURE_ID = "async-cache-v1"
FIXTURE = ROOT / "fixtures" / "async-cache-v1"
STARTER = FIXTURE / "starter"
HIDDEN = FIXTURE / "hidden" / "Cache.HiddenTests"
REFERENCE = FIXTURE / "reference" / "AsyncExpiringCache.cs"
MANIFEST = FIXTURE / "fixture-manifest.json"
MATRIX = ROOT / "matrix.json"
RESULT_SCHEMA = ROOT / "schemas" / "run-result.schema.json"
EVIDENCE_SCHEMA = ROOT / "schemas" / "evidence-bundle.schema.json"
RUNS = ROOT / "runs"
EVALUATOR_IMAGE = "codex-bench-evaluator:10.0.301"
GENERATOR_IMAGE = "codex-bench-generator:0.147.0"
IGNORED_PARTS = {"bin", "obj", ".git", ".nuget", ".dotnet-home", "__pycache__"}
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
VERIFY_RUN_ID_PATTERN = re.compile(r"^verify-[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_EVALUATOR_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_GENERATOR_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_EVALUATION_TIMEOUT_SECONDS = 90
AGENT_PROMPT = (
    "Open TASK.md and complete the repair exactly as specified. "
    "Work only in this directory. Do not inspect external files or use the network. "
    "Run the required public tests. Complete the work directly without spawning agents. "
    "Return a concise summary, commands run, and elapsed wall time."
)
DETERMINISTIC_ENV = {
    "TZ": "UTC",
    "LANG": "C",
    "LC_ALL": "C",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
    "DOTNET_NOLOGO": "1",
    "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
}
POLICY_PATTERNS = {
    "blocking Result": re.compile(r"\.Result\b"),
    "blocking Wait": re.compile(r"\.Wait\s*\("),
    "GetAwaiter/GetResult": re.compile(r"GetAwaiter\s*\(\s*\)\s*\.\s*GetResult\s*\("),
    "Thread.Sleep": re.compile(r"Thread\s*\.\s*Sleep\s*\("),
    "Task.Delay": re.compile(r"Task\s*\.\s*Delay\s*\("),
    "process termination": re.compile(r"Environment\s*\.\s*(?:Exit|FailFast)\s*\("),
    "module initializer": re.compile(r"\bModuleInitializer\b"),
    "native interop": re.compile(r"\b(?:DllImport|LibraryImport)\b"),
    "process launch": re.compile(r"\bProcess\s*\.\s*Start\s*\("),
    "assembly loading": re.compile(r"\bAssembly\s*\.\s*(?:Load|LoadFrom|LoadFile)\s*\("),
    "network client": re.compile(
        r"\b(?:HttpClient|WebRequest|Socket|TcpClient|UdpClient|Dns)\b"
    ),
    "blocking synchronization": re.compile(
        r"\b(?:Task\s*\.\s*(?:WaitAll|WaitAny)|Monitor\s*\.\s*Wait|Thread\s*\.\s*Join)\s*\("
    ),
}
PUBLIC_PASS_MARKER = "CODEX_BENCH_PUBLIC_PASS_V1"
HIDDEN_PASS_MARKER = "CODEX_BENCH_HIDDEN_PASS_V1"
HIDDEN_BEHAVIORS = (
    "constructor validation",
    "same-key misses are single flight",
    "different keys load concurrently",
    "TTL starts at successful completion and expires at boundary",
    "failed load is retried",
    "one cancelled waiter does not cancel shared load",
    "all waiters may cancel while successful load remains cached",
    "invalidation supersedes an older in-flight generation",
    "public API contract",
)
BEHAVIOR_ROLLUPS = ("PASS", "FAIL", "NOT_RUN", "AMBIGUOUS", "NO_HIDDEN_RUN")
INFRA_FAILURE_KINDS = {
    "launcher", "timeout-anomaly", "build-timeout", "fixture-manifest", "generation-launcher",
    "generation-inert", "generation-unavailable",
}
CANDIDATE_FAILURE_KINDS = {
    "timeout", "output-limit", "test", "build", "test-marker", "integrity", "policy",
    "generation-timeout", "generation-output-limit", "generation-exit",
    "generation-unchanged",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_value_hash(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def json_file_hash(path: Path) -> str:
    return json_value_hash(json.loads(path.read_text(encoding="utf-8")))


def visible_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in IGNORED_PARTS for part in path.relative_to(root).parts)
    )


def fixture_file_hashes() -> dict[str, str]:
    files: dict[str, str] = {}
    for path in visible_files(FIXTURE):
        relative = path.relative_to(FIXTURE).as_posix()
        if relative == MANIFEST.name:
            continue
        files[relative] = sha256_file(path)
    return files


def write_manifest() -> dict[str, Any]:
    payload = {
        "schemaVersion": 1,
        "fixtureId": FIXTURE_ID,
        "files": fixture_file_hashes(),
    }
    MANIFEST.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def verify_manifest() -> tuple[bool, list[str]]:
    if not MANIFEST.exists():
        return False, ["fixture-manifest.json is missing"]
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = fixture_file_hashes()
    errors: list[str] = []
    expected_files = expected.get("files", {})
    for path in sorted(set(expected_files) | set(actual)):
        if path not in expected_files:
            errors.append(f"unexpected fixture file: {path}")
        elif path not in actual:
            errors.append(f"missing fixture file: {path}")
        elif expected_files[path] != actual[path]:
            errors.append(f"fixture hash mismatch: {path}")
    return not errors, errors


def ensure_strict_descendant(path: Path, parent: Path) -> Path:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"Refusing path that is not strictly beneath {root}: {resolved}")
    return resolved


def validate_run_id(run_id: str, *, allow_verify: bool = False) -> str:
    valid = RUN_ID_PATTERN.fullmatch(run_id) or (
        allow_verify and VERIFY_RUN_ID_PATTERN.fullmatch(run_id)
    )
    if not valid:
        raise ValueError(f"Invalid opaque run ID: {run_id!r}")
    return run_id


def run_artifact_path(parent: Path, run_id: str, suffix: str) -> Path:
    safe_id = validate_run_id(run_id)
    return ensure_strict_descendant(parent / f"{safe_id}{suffix}", parent)


def load_or_create_id_key(path: Path) -> bytes:
    path = ensure_strict_descendant(path, RUNS)
    if path.exists():
        value = path.read_bytes()
        if len(value) < 32:
            raise ValueError("ID key must contain at least 32 bytes")
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_bytes(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
    return value


def copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return set(names) & IGNORED_PARTS


def materialize(destination: Path, *, replace: bool = False) -> None:
    if destination.is_symlink():
        raise ValueError("Destination must not be a symbolic link")
    destination = destination.resolve()
    if destination.exists():
        if not replace:
            raise FileExistsError(f"Destination already exists: {destination}")
        ensure_strict_descendant(destination, RUNS / "workspaces")
        shutil.rmtree(destination)
    shutil.copytree(STARTER, destination, ignore=copy_ignore)
    shutil.copy2(FIXTURE / "TASK.md", destination / "TASK.md")
    nuget_config = ROOT / "NuGet.config"
    if nuget_config.exists():
        shutil.copy2(nuget_config, destination / nuget_config.name)


def candidate_integrity(candidate: Path) -> list[str]:
    errors: list[str] = []
    if candidate.is_symlink():
        return ["candidate root must not be a symbolic link"]
    for path in candidate.rglob("*"):
        if path.is_symlink():
            errors.append(f"symbolic links are forbidden: {path.relative_to(candidate).as_posix()}")
    if errors:
        return errors
    required = {
        "TASK.md": FIXTURE / "TASK.md",
        "Cache.Core/Cache.Core.csproj": STARTER / "Cache.Core" / "Cache.Core.csproj",
        "Cache.PublicTests/Cache.PublicTests.csproj": STARTER / "Cache.PublicTests" / "Cache.PublicTests.csproj",
        "Cache.PublicTests/Program.cs": STARTER / "Cache.PublicTests" / "Program.cs",
    }
    nuget_config = ROOT / "NuGet.config"
    if nuget_config.exists():
        required[nuget_config.name] = nuget_config
    for relative, expected in required.items():
        actual = candidate / relative
        if not actual.exists():
            errors.append(f"missing immutable file: {relative}")
        elif sha256_file(actual) != sha256_file(expected):
            errors.append(f"modified immutable file: {relative}")

    if not (candidate / "Cache.Core" / "AsyncExpiringCache.cs").exists():
        errors.append("missing implementation: Cache.Core/AsyncExpiringCache.cs")

    allowed_exact = set(required)
    for path in visible_files(candidate):
        relative = path.relative_to(candidate).as_posix()
        if relative in allowed_exact:
            continue
        if relative == "Cache.Core/AsyncExpiringCache.cs":
            try:
                path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"non-UTF-8 source: {relative}")
            continue
        errors.append(f"unexpected candidate file: {relative}")
    return errors


def candidate_hash(candidate: Path) -> str:
    entries = {
        path.relative_to(candidate).as_posix(): sha256_file(path)
        for path in visible_files(candidate / "Cache.Core")
        if path.suffix == ".cs"
    }
    return sha256_bytes(canonical_json(entries).encode("utf-8"))


def policy_violations(candidate: Path) -> list[str]:
    violations: list[str] = []
    for path in visible_files(candidate / "Cache.Core"):
        if path.suffix != ".cs":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            violations.append(f"non-UTF-8 source: {path.relative_to(candidate).as_posix()}")
            continue
        for name, pattern in POLICY_PATTERNS.items():
            if pattern.search(text):
                violations.append(f"{name}: {path.relative_to(candidate).as_posix()}")
    return violations


def repository_provenance() -> dict[str, Any]:
    tracked = [
        ROOT / "harness.py",
        ROOT / "matrix.json",
        ROOT / "schemas" / "run-result.schema.json",
        ROOT / "schemas" / "evidence-bundle.schema.json",
        ROOT / "global.json",
        ROOT / "Directory.Build.props",
        ROOT / "NuGet.config",
        ROOT / "docker" / "evaluator.Dockerfile",
        ROOT / "docker" / "generator.Dockerfile",
        ROOT / "docker" / "package.json",
        ROOT / "docker" / "package-lock.json",
        ROOT / "skills" / "orchestrate" / "SKILL.md",
        ROOT / "skills" / "orchestrate" / "agents" / "openai.yaml",
        MANIFEST,
    ]
    commit = None
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    if completed.returncode == 0:
        commit = completed.stdout.strip()
    return {
        "gitCommit": commit,
        "files": {path.relative_to(ROOT).as_posix(): sha256_file(path) for path in tracked},
        "sdkVersion": json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]["version"],
    }


def docker_identity_arguments() -> list[str]:
    if os.name != "posix" or not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        return []
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


def docker_tmpfs(path: str, options: str) -> str:
    if os.name == "posix" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        options += f",uid={os.getuid()},gid={os.getgid()}"
    return f"{path}:{options}"


def base_command(
    command: list[str], cwd: Path, backend: str, container_name: str, *, readonly: bool
) -> list[str]:
    if backend == "native":
        return command
    if backend != "docker":
        raise ValueError(f"Unknown backend: {backend}")
    mount = f"type=bind,src={cwd.resolve()},dst=/workspace"
    if readonly:
        mount += ",readonly"
    return [
        "docker", "run", "--rm", "--network", "none", "--name", container_name,
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "256", "--memory", "1g", "--cpus", "2",
        *docker_identity_arguments(),
        "--tmpfs", docker_tmpfs("/tmp", "rw,noexec,nosuid,size=128m"),
        "--mount", mount,
        "--workdir", "/workspace",
        *sum((["--env", f"{key}={value}"] for key, value in DETERMINISTIC_ENV.items()), []),
        "--env", f"DOTNET_CLI_HOME={'/tmp/dotnet-home' if readonly else '/workspace/.dotnet-home'}",
        "--env", f"NUGET_PACKAGES={'/tmp/nuget' if readonly else '/workspace/.nuget'}",
        EVALUATOR_IMAGE,
        *command,
    ]


def sanitized_environment(cwd: Path) -> dict[str, str]:
    allowed = ["PATH", "SystemRoot", "WINDIR", "TMPDIR", "TEMP", "TMP", "DOTNET_ROOT"]
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(DETERMINISTIC_ENV)
    environment["DOTNET_CLI_HOME"] = str(cwd / ".dotnet-home")
    environment["NUGET_PACKAGES"] = str(cwd / ".nuget")
    return environment


def execute_captured(
    command: list[str], *, cwd: Path, environment: dict[str, str] | None,
    timeout: int, limit: int, input_bytes: bytes | None = None,
    timeout_callback: Any = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=cwd, env=environment,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError) as exception:
        return {
            "exitCode": None, "stdout": "", "stderr": str(exception),
            "timedOut": False, "launcherFailure": True,
            "outputLimitExceeded": False,
            "durationSeconds": round(time.monotonic() - started, 6),
        }

    buffers = [bytearray(), bytearray()]
    output_limit_exceeded = False
    output_bytes = 0
    output_lock = threading.Lock()
    stopped = threading.Event()

    def stop_for_limit() -> None:
        if stopped.is_set():
            return
        stopped.set()
        if timeout_callback is not None:
            timeout_callback()
        try:
            process.kill()
        except ProcessLookupError:
            pass

    def drain(stream: Any, index: int) -> None:
        nonlocal output_bytes, output_limit_exceeded
        while True:
            block = stream.read(65536)
            if not block:
                return
            should_stop = False
            with output_lock:
                remaining = max(0, limit - output_bytes)
                accepted = min(len(block), remaining)
                if accepted:
                    buffers[index].extend(block[:accepted])
                    output_bytes += accepted
                if accepted < len(block):
                    output_limit_exceeded = True
                    should_stop = True
            if should_stop:
                stop_for_limit()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
    ]
    for thread in threads:
        thread.start()
    if input_bytes is not None and process.stdin is not None:
        process.stdin.write(input_bytes)
        process.stdin.close()
    timed_out = False
    try:
        exit_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_for_limit()
        exit_code = process.wait()
    for thread in threads:
        thread.join(timeout=5)
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()
    return {
        "exitCode": None if timed_out else exit_code,
        "stdout": bytes(buffers[0]).decode("utf-8", errors="replace"),
        "stderr": bytes(buffers[1]).decode("utf-8", errors="replace"),
        "timedOut": timed_out,
        "launcherFailure": False,
        "outputLimitExceeded": output_limit_exceeded,
        "durationSeconds": round(time.monotonic() - started, 6),
    }


def run_process(
    command: list[str], cwd: Path, timeout: int, backend: str, *, readonly: bool = False
) -> dict[str, Any]:
    container_name = f"codex-bench-eval-{os.getpid()}-{time.monotonic_ns()}"
    actual = base_command(command, cwd, backend, container_name, readonly=readonly)
    environment = sanitized_environment(cwd)
    def stop_container() -> None:
        if backend == "docker":
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    result = execute_captured(
        actual, cwd=cwd, environment=environment, timeout=timeout,
        limit=MAX_EVALUATOR_OUTPUT_BYTES, timeout_callback=stop_container,
    )
    result["command"] = command
    if backend == "docker" and result["exitCode"] == 125:
        result["launcherFailure"] = True
    return result


def run_with_timeout_confirmation(
    command: list[str], cwd: Path, timeout: int, backend: str, *, readonly: bool = False
) -> dict[str, Any]:
    first = run_process(command, cwd, timeout, backend, readonly=readonly)
    if not first["timedOut"]:
        return {"attempts": [first], "hadTimeout": False, "confirmedTimeout": False}
    second = run_process(command, cwd, timeout * 2, backend, readonly=readonly)
    return {
        "attempts": [first, second],
        "hadTimeout": True,
        "confirmedTimeout": bool(second["timedOut"]),
    }


def last_attempt(run: dict[str, Any]) -> dict[str, Any]:
    return run["attempts"][-1]


def execution_failure(
    execution: dict[str, Any], *, candidate_execution: bool
) -> tuple[str, str] | None:
    attempt = last_attempt(execution)
    if attempt["launcherFailure"]:
        return "INFRA_FAILURE", "launcher"
    if execution["hadTimeout"]:
        if not execution["confirmedTimeout"]:
            return "INFRA_FAILURE", "timeout-anomaly"
        if candidate_execution:
            return "CANDIDATE_FAILURE", "timeout"
        return "INFRA_FAILURE", "build-timeout"
    if attempt["outputLimitExceeded"]:
        return "CANDIDATE_FAILURE", "output-limit"
    if attempt["exitCode"] != 0:
        return "CANDIDATE_FAILURE", "test" if candidate_execution else "build"
    return None


def extract_behavior_outcomes(output: str) -> list[dict[str, str]]:
    lines = output.splitlines()
    outcomes: list[dict[str, str]] = []
    for behavior in HIDDEN_BEHAVIORS:
        markers = [
            "PASS" for line in lines if line == f"PASS {behavior}"
        ] + [
            "FAIL" for line in lines if line.startswith(f"FAIL {behavior}:")
        ]
        if not markers:
            outcome = "NOT_RUN"
        elif len(markers) != 1:
            outcome = "AMBIGUOUS"
        else:
            outcome = markers[0]
        outcomes.append({"name": behavior, "outcome": outcome})
    return outcomes


def result_behavior_outcomes(result: dict[str, Any]) -> dict[str, str]:
    """Collapse one generation's per-repetition behavior vectors into one outcome each.

    A failure in any repetition dominates, because an artifact that only sometimes holds a
    behavior does not hold it. A behavior that passed whenever it was reached counts as a
    pass; one never reached -- every repetition stopped earlier -- stays censored.
    """
    repetitions = [
        {behavior["name"]: behavior["outcome"] for behavior in test["behaviors"]}
        for test in result["tests"]
        if test["name"].startswith("hidden-") and test["name"] != "hidden-build"
    ]
    if not repetitions:
        return {behavior: "NO_HIDDEN_RUN" for behavior in HIDDEN_BEHAVIORS}
    collapsed: dict[str, str] = {}
    for behavior in HIDDEN_BEHAVIORS:
        outcomes = {repetition.get(behavior, "NOT_RUN") for repetition in repetitions}
        for kind in ("FAIL", "AMBIGUOUS", "PASS"):
            if kind in outcomes:
                collapsed[behavior] = kind
                break
        else:
            collapsed[behavior] = "NOT_RUN"
    return collapsed


def behavior_summary(results: list[dict[str, Any]], planned: int) -> dict[str, Any]:
    """Per-behavior pass rates over the planned denominator.

    The binary status of a generation carries one bit; the hidden suite measures multiple
    independent behaviors. Reporting them separately makes adjacent configurations separable
    without multiplying the number of generations.
    """
    rollups = [result_behavior_outcomes(result) for result in results]
    summary: dict[str, Any] = {}
    for behavior in HIDDEN_BEHAVIORS:
        observed = [rollup[behavior] for rollup in rollups]
        counts = {kind: observed.count(kind) for kind in BEHAVIOR_ROLLUPS}
        low, high = wilson(counts["PASS"], planned)
        summary[behavior] = {
            "plannedRuns": planned,
            "passes": counts["PASS"],
            "failures": counts["FAIL"],
            "notRun": counts["NOT_RUN"],
            "ambiguous": counts["AMBIGUOUS"],
            "withoutHiddenRun": counts["NO_HIDDEN_RUN"],
            "missing": planned - len(results),
            "passRate": counts["PASS"] / planned if planned else None,
            "wilson95": [low, high] if planned else None,
        }
    return summary


def evaluate_candidate(
    candidate: Path,
    *,
    run_id: str,
    model: str,
    effort: str,
    machine: str,
    repeat: int,
    timeout: int,
    backend: str,
    isolation: str,
    trusted: bool = False,
    record_kind: str = "external-evaluation",
    campaign_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if repeat <= 0 or timeout <= 0:
        raise ValueError("repeat and timeout must be positive integers")
    validate_run_id(
        run_id,
        allow_verify=record_kind == "external-evaluation"
        and trusted
        and isolation == "fixture-verification",
    )
    if record_kind not in {"campaign", "external-evaluation"}:
        raise ValueError(f"Unsupported result record kind: {record_kind}")
    if record_kind == "campaign" and campaign_metadata is None:
        raise ValueError("Campaign evaluations require campaign metadata")
    if record_kind == "external-evaluation" and campaign_metadata is not None:
        raise ValueError("External evaluations must not carry campaign metadata")
    if backend == "native" and not trusted:
        raise ValueError("Native evaluation is restricted to trusted fixture verification")
    started = time.monotonic()
    integrity = candidate_integrity(candidate)
    policy = policy_violations(candidate) if not integrity else []
    manifest_ok, manifest_errors = verify_manifest()
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "recordKind": record_kind,
        "runId": run_id,
        "fixtureId": FIXTURE_ID,
        "fixtureManifestHash": json_file_hash(MANIFEST) if MANIFEST.exists() else None,
        "candidateHash": candidate_hash(candidate) if not integrity else None,
        "promptHash": sha256_bytes(AGENT_PROMPT.encode("utf-8")),
        "model": model,
        "reasoningEffort": effort,
        "machineId": machine,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "provenance": repository_provenance(),
        "backend": backend,
        "evaluatorImage": docker_image_info(EVALUATOR_IMAGE) if backend == "docker" else None,
        "generatorImage": None,
        "isolation": isolation,
        "repeatCount": repeat,
        "integrityViolations": integrity,
        "policyViolations": policy,
        "manifestErrors": manifest_errors,
        "tests": [],
        "failureKind": None,
    }
    if campaign_metadata is not None:
        result.update(campaign_metadata)

    def finish(status: str, failure_kind: str | None) -> dict[str, Any]:
        result["status"] = status
        result["failureKind"] = failure_kind
        result["durationSeconds"] = round(time.monotonic() - started, 6)
        validate_result(result)
        return result

    if not manifest_ok:
        return finish("INFRA_FAILURE", "fixture-manifest")
    if integrity:
        return finish("CANDIDATE_FAILURE", "integrity")
    if policy:
        return finish("CANDIDATE_FAILURE", "policy")

    with tempfile.TemporaryDirectory(prefix="codex-bench-eval-") as temp_name:
        root = Path(temp_name)
        workspace = root / "candidate-build"
        shutil.copytree(candidate, workspace, ignore=copy_ignore)
        shutil.copy2(ROOT / "global.json", workspace / "global.json")
        shutil.copy2(ROOT / "Directory.Build.props", workspace / "Directory.Build.props")
        nuget_config = ROOT / "NuGet.config"
        if nuget_config.exists():
            shutil.copy2(nuget_config, workspace / nuget_config.name)
        status = "PASS"
        failure_kind: str | None = None
        public_runtime = workspace / "public-published"
        public_build_command = [
            "dotnet", "publish", "Cache.PublicTests/Cache.PublicTests.csproj",
            "--configuration", "Release", "--output", "public-published",
        ]
        execution = run_with_timeout_confirmation(public_build_command, workspace, timeout, backend)
        result["tests"].append({"name": "public-build", **execution})
        failure = execution_failure(execution, candidate_execution=False)
        if failure:
            status, failure_kind = failure

        if status == "PASS":
            public_command = ["dotnet", "Cache.PublicTests.dll"]
            execution = run_with_timeout_confirmation(
                public_command, public_runtime, timeout, backend, readonly=backend == "docker"
            )
            result["tests"].append({"name": "public", **execution})
            failure = execution_failure(execution, candidate_execution=True)
            if failure:
                status, failure_kind = failure
            elif last_attempt(execution)["stdout"].count(PUBLIC_PASS_MARKER) != 1:
                status, failure_kind = "CANDIDATE_FAILURE", "test-marker"

        runtime = root / "hidden-build" / "published"
        if status == "PASS":
            hidden_build = root / "hidden-build"
            shutil.copytree(HIDDEN, hidden_build / "Cache.HiddenTests", ignore=copy_ignore)
            candidate_binary = hidden_build / "Candidate"
            candidate_binary.mkdir(parents=True)
            built_core = workspace / "Cache.Core" / "bin" / "Release" / "net10.0"
            for path in built_core.glob("Cache.Core.*"):
                if path.is_file():
                    shutil.copy2(path, candidate_binary / path.name)
            shutil.copy2(ROOT / "global.json", hidden_build / "global.json")
            shutil.copy2(ROOT / "Directory.Build.props", hidden_build / "Directory.Build.props")
            if nuget_config.exists():
                shutil.copy2(nuget_config, hidden_build / nuget_config.name)
            publish_command = [
                "dotnet", "publish", "Cache.HiddenTests/Cache.HiddenTests.csproj",
                "--configuration", "Release",
                "--output", "published",
                "-p:CandidateAssemblyPath=../Candidate/Cache.Core.dll",
            ]
            execution = run_with_timeout_confirmation(publish_command, hidden_build, timeout, backend)
            result["tests"].append({"name": "hidden-build", **execution})
            failure = execution_failure(execution, candidate_execution=False)
            if failure:
                status, failure_kind = failure

        if status == "PASS":
            hidden_command = ["dotnet", "Cache.HiddenTests.dll"]
            for index in range(1, repeat + 1):
                execution = run_with_timeout_confirmation(
                    hidden_command, runtime, timeout, backend, readonly=backend == "docker"
                )
                result["tests"].append({
                    "name": f"hidden-{index}",
                    "behaviors": extract_behavior_outcomes(last_attempt(execution)["stdout"]),
                    **execution,
                })
                failure = execution_failure(execution, candidate_execution=True)
                if failure:
                    status, failure_kind = failure
                    break
                if last_attempt(execution)["stdout"].count(HIDDEN_PASS_MARKER) != 1:
                    status, failure_kind = "CANDIDATE_FAILURE", "test-marker"
                    break
    return finish(status, failure_kind)


def atomic_write(
    path: Path, value: bytes, *, mode: int = 0o644, replace: bool = True
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        stream = os.fdopen(descriptor, "wb")
        descriptor = -1
        with stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
            temporary.unlink()
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def save_json(
    path: Path, value: Any, *, private: bool = False, replace: bool = True
) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write(path, encoded, mode=0o600 if private else 0o644, replace=replace)


def image_spec_hash(dockerfile: str) -> str:
    paths = [ROOT / dockerfile]
    if "generator" in dockerfile:
        paths.extend([ROOT / "docker" / "package.json", ROOT / "docker" / "package-lock.json"])
    payload = {path.relative_to(ROOT).as_posix(): sha256_file(path) for path in paths}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def image_spec_hash_from_provenance(provenance: dict[str, Any], dockerfile: str) -> str:
    keys = [dockerfile]
    if "generator" in dockerfile:
        keys.extend(["docker/package.json", "docker/package-lock.json"])
    files = provenance.get("files")
    if not isinstance(files, dict) or any(key not in files for key in keys):
        raise ValueError(f"Provenance lacks image build inputs for {dockerfile}")
    return sha256_bytes(canonical_json({key: files[key] for key in keys}).encode("utf-8"))


def docker_image_info(image: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "image", "inspect", image],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    value = json.loads(completed.stdout)[0]
    return {
        "tag": image,
        "id": value["Id"],
        "repoDigests": value.get("RepoDigests") or [],
        "os": value.get("Os"),
        "architecture": value.get("Architecture"),
        "specHash": (value.get("Config", {}).get("Labels") or {}).get("io.codex-bench.spec-sha"),
    }


def build_one_image(image: str, dockerfile: str) -> None:
    spec_hash = image_spec_hash(dockerfile)
    subprocess.run(
        [
            "docker", "build", "--file", dockerfile, "--tag", image,
            "--label", f"io.codex-bench.spec-sha={spec_hash}", ".",
        ],
        cwd=ROOT,
        check=True,
    )


def build_images() -> None:
    build_one_image(EVALUATOR_IMAGE, "docker/evaluator.Dockerfile")
    build_one_image(GENERATOR_IMAGE, "docker/generator.Dockerfile")


def ensure_image(image: str, dockerfile: str) -> None:
    found = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if found.returncode == 0:
        info = docker_image_info(image)
        if info["specHash"] == image_spec_hash(dockerfile):
            return
    build_one_image(image, dockerfile)


def benchmark_auth_file(explicit: Path | None) -> Path:
    configured = explicit or (Path(os.environ["CODEX_BENCH_AUTH_FILE"]) if "CODEX_BENCH_AUTH_FILE" in os.environ else None)
    if configured is None:
        raise ValueError(
            "A dedicated benchmark credential is required via --auth-file or CODEX_BENCH_AUTH_FILE; "
            "the persistent default Codex login is intentionally not used"
        )
    expanded = configured.expanduser()
    if expanded.is_symlink():
        raise ValueError("Benchmark auth path must be a regular, non-symlink file")
    path = expanded.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Benchmark auth file not found: {path}")
    if not path.is_file():
        raise ValueError("Benchmark auth path must be a regular, non-symlink file")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise PermissionError("Benchmark auth file must not be accessible by group or other users")
    return path


GENERATOR_SANDBOX_MODE = "danger-full-access"
NAMESPACE_SANDBOX_MODES = {"read-only", "workspace-write"}


def generator_container_arguments(
    candidate: Path, container_name: str, *, auth: Path | None, interactive: bool
) -> list[str]:
    """Container flags shared by real generations and the credential-free CI probe.

    Keeping one constructor is the point: these flags previously diverged from the
    evaluator's and left the SDK with no writable home, which no test could see.
    """
    return [
        "docker", "run", "--rm", *(["--interactive"] if interactive else []),
        "--name", container_name,
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "512", "--memory", "2g", "--cpus", "4",
        *docker_identity_arguments(),
        "--tmpfs", docker_tmpfs("/tmp", "rw,noexec,nosuid,size=256m"),
        "--tmpfs", docker_tmpfs("/codex-home", "rw,noexec,nosuid,size=32m"),
        "--env", "CODEX_HOME=/codex-home",
        # The read-only rootfs and --user mean HOME is not writable, so the SDK must be
        # pointed at the read-write workspace or every dotnet invocation fails before the
        # agent can build or test the code it writes. IGNORED_PARTS excludes both paths.
        "--env", "DOTNET_CLI_HOME=/workspace/.dotnet-home",
        "--env", "NUGET_PACKAGES=/workspace/.nuget",
        *(
            ["--mount", f"type=bind,src={auth},dst=/codex-home/auth.json,readonly"]
            if auth is not None else []
        ),
        "--mount", f"type=bind,src={candidate.resolve()},dst=/workspace",
        "--workdir", "/workspace",
    ]


def codex_exec_arguments(model: str, effort: str, target: str = "-") -> list[str]:
    """Single source of truth for the Codex CLI routing arguments."""
    return [
        "codex", "exec",
        # Docker provides the actual sandbox; see generator_container_arguments.
        "--sandbox", GENERATOR_SANDBOX_MODE,
        "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--json", "--model", model,
        "--config", f'model_reasoning_effort="{effort}"',
        target,
    ]


def generate_candidate(
    job: dict[str, Any], *, auth_path: Path | None, replace: bool = False
) -> Path:
    run_id = validate_run_id(job["runId"])
    resolved_auth = benchmark_auth_file(auth_path)
    ensure_image(GENERATOR_IMAGE, "docker/generator.Dockerfile")
    candidate = ensure_strict_descendant(RUNS / "workspaces" / run_id, RUNS / "workspaces")
    materialize(candidate, replace=replace)
    log_dir = RUNS / "generations"
    log_dir.mkdir(parents=True, exist_ok=True)
    container_name = f"codex-bench-gen-{run_id}"
    command = [
        *generator_container_arguments(
            candidate, container_name, auth=resolved_auth, interactive=True
        ),
        GENERATOR_IMAGE,
        *codex_exec_arguments(job["model"], job["reasoningEffort"]),
    ]
    def stop_container() -> None:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    completed = execute_captured(
        command, cwd=ROOT, environment=None,
        timeout=int(job.get("agentTimeoutSeconds", 1800)),
        limit=MAX_GENERATOR_OUTPUT_BYTES,
        input_bytes=AGENT_PROMPT.encode("utf-8"),
        timeout_callback=stop_container,
    )
    if completed["exitCode"] == 125:
        completed["launcherFailure"] = True
    log_path = run_artifact_path(log_dir, run_id, ".jsonl")
    atomic_write(log_path, completed["stdout"].encode("utf-8"), mode=0o600)
    metadata = {
        "runId": run_id,
        "exitCode": completed["exitCode"],
        "durationSeconds": completed["durationSeconds"],
        "stderr": completed["stderr"],
        "timedOut": completed["timedOut"],
        "launcherFailure": completed["launcherFailure"],
        "outputLimitExceeded": completed["outputLimitExceeded"],
        "promptHash": sha256_bytes(AGENT_PROMPT.encode("utf-8")),
        "generatorImage": docker_image_info(GENERATOR_IMAGE),
        "isolation": "container-strong",
    }
    metadata_path = run_artifact_path(log_dir, run_id, ".meta.json")
    save_json(metadata_path, metadata, private=True)
    return candidate


def pristine_candidate_hash() -> str:
    return candidate_hash(STARTER)


def check_generator(timeout: int) -> dict[str, Any]:
    """Exercise the shared container envelope and parse real Codex argv without a request.

    Both generator faults found so far were invisible to every other check: the SDK had no
    writable home, and Codex's namespace sandbox could not start. Neither touched the
    evaluator, the fixture, or any unit test, and the second one still produced results that
    looked like ordinary model failures.
    """
    ensure_image(GENERATOR_IMAGE, "docker/generator.Dockerfile")
    expected_version = json.loads(
        (ROOT / "docker" / "package.json").read_text(encoding="utf-8")
    )["dependencies"]["@openai/codex"]
    probe = (
        'echo "PROBE_CODEX_VERSION=$(codex --version 2>&1 | tail -1)"; '
        'BW=$(find /opt/codex -name bwrap -type f 2>/dev/null | head -1); '
        'if [ -n "$BW" ] && "$BW" --unshare-user --dev-bind / / true 2>/dev/null; '
        'then echo PROBE_NAMESPACE=available; else echo PROBE_NAMESPACE=unavailable; fi; '
        'dotnet run --project Cache.PublicTests/Cache.PublicTests.csproj 2>&1 | tail -5'
    )
    argv_executions: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="codex-bench-genprobe-") as temporary:
        candidate = Path(temporary) / "probe"
        materialize(candidate)
        container_name = f"codex-bench-genprobe-{os.getpid()}-{time.monotonic_ns()}"
        command = [
            *generator_container_arguments(
                candidate, container_name, auth=None, interactive=False
            ),
            GENERATOR_IMAGE, "sh", "-lc", probe,
        ]

        def stop_container() -> None:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )

        execution = execute_captured(
            command, cwd=ROOT, environment=None, timeout=timeout,
            limit=MAX_EVALUATOR_OUTPUT_BYTES, timeout_callback=stop_container,
        )
        for index, configuration in enumerate(load_matrix()):
            argv_container_name = f"{container_name}-argv-{index}"
            argv_command = [
                *generator_container_arguments(
                    candidate, argv_container_name, auth=None, interactive=False
                ),
                GENERATOR_IMAGE,
                *codex_exec_arguments(
                    configuration["model"], configuration["reasoningEffort"], "--help"
                ),
            ]

            def stop_argv_container(name: str = argv_container_name) -> None:
                subprocess.run(
                    ["docker", "rm", "-f", name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )

            argv_executions.append(execute_captured(
                argv_command, cwd=ROOT, environment=None, timeout=timeout,
                limit=MAX_EVALUATOR_OUTPUT_BYTES, timeout_callback=stop_argv_container,
            ))

    def probed(prefix: str) -> str | None:
        for line in execution["stdout"].splitlines():
            if line.startswith(prefix):
                return line.split("=", 1)[1].strip()
        return None

    observed_version = probed("PROBE_CODEX_VERSION=")
    namespaces = probed("PROBE_NAMESPACE=")
    checks = {
        # The agent is told to build and run the public suite; if it cannot, every
        # generation is produced blind.
        "toolchainRuns": execution["stdout"].count(PUBLIC_PASS_MARKER) == 1,
        "codexVersionMatches": observed_version == f"codex-cli {expected_version}",
        "codexArgvAccepted": all(
            item["exitCode"] == 0
            and not item["timedOut"]
            and not item["launcherFailure"]
            and not item["outputLimitExceeded"]
            for item in argv_executions
        ),
        # Only demand namespaces when the configured sandbox mode actually needs them, so
        # this stays correct if either the mode or the container policy changes.
        "sandboxModeSupported": (
            GENERATOR_SANDBOX_MODE not in NAMESPACE_SANDBOX_MODES
            or namespaces == "available"
        ),
    }
    return {
        "schemaVersion": 1,
        "generatorImage": docker_image_info(GENERATOR_IMAGE),
        "sandboxMode": GENERATOR_SANDBOX_MODE,
        "expectedCodexVersion": expected_version,
        "observedCodexVersion": observed_version,
        "namespaceSandbox": namespaces,
        "checks": checks,
        "passed": (
            all(checks.values())
            and execution["exitCode"] == 0
            and not execution["timedOut"]
            and not execution["launcherFailure"]
            and not execution["outputLimitExceeded"]
        ),
        "exitCode": execution["exitCode"],
        "timedOut": execution["timedOut"],
        "durationSeconds": execution["durationSeconds"],
        "stdout": execution["stdout"],
        "stderr": execution["stderr"],
        "provenance": repository_provenance(),
    }


def generation_log_evidence(log_path: Path) -> dict[str, Any]:
    """Parse terminal and command evidence from the Codex JSONL protocol."""
    evidence = {
        "successfulCommands": 0,
        "turnCompleted": False,
        "turnFailed": False,
        "valid": True,
    }
    try:
        lines = log_path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        evidence["valid"] = False
        return evidence
    phase = "start"
    terminal_events = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            evidence["valid"] = False
            continue
        if not isinstance(event, dict):
            evidence["valid"] = False
            continue
        event_type = event.get("type")
        if terminal_events:
            evidence["valid"] = False
        if event_type == "thread.started":
            if phase != "start":
                evidence["valid"] = False
            phase = "thread"
        elif event_type == "turn.started":
            if phase != "thread":
                evidence["valid"] = False
            phase = "turn"
        elif event_type == "turn.completed":
            if phase != "turn":
                evidence["valid"] = False
            evidence["turnCompleted"] = True
            terminal_events += 1
            phase = "terminal"
        elif event_type in {"turn.failed", "error"}:
            if phase != "turn":
                evidence["valid"] = False
            evidence["turnFailed"] = True
            terminal_events += 1
            phase = "terminal"
        elif not str(event_type).startswith("item.") or phase != "turn":
            evidence["valid"] = False
        item = event.get("item")
        if event_type == "item.completed" and isinstance(item, dict) \
                and item.get("type") == "command_execution" \
                and item.get("exit_code") == 0:
            evidence["successfulCommands"] += 1
    if terminal_events != 1 or evidence["turnCompleted"] == evidence["turnFailed"]:
        evidence["valid"] = False
    return evidence


def generation_command_successes(log_path: Path) -> int:
    """Compatibility helper for tests and reporting."""
    return int(generation_log_evidence(log_path)["successfulCommands"])


def generation_failure_kind(
    metadata: dict[str, Any],
    candidate: Path | None = None,
    log_path: Path | None = None,
) -> tuple[str, str] | None:
    if metadata.get("launcherFailure"):
        return "INFRA_FAILURE", "generation-launcher"
    if metadata.get("timedOut"):
        return "CANDIDATE_FAILURE", "generation-timeout"
    if metadata.get("outputLimitExceeded"):
        return "CANDIDATE_FAILURE", "generation-output-limit"
    log_evidence = generation_log_evidence(log_path) if log_path is not None else None
    artifact_sampled = False
    if candidate is not None:
        artifact_sampled = (
            candidate_hash(candidate) != pristine_candidate_hash()
            or bool(candidate_integrity(candidate))
        )
    if metadata.get("exitCode") != 0:
        # A non-zero Codex exit is not automatically evidence about the model. Auth,
        # service, transport, and CLI failures commonly exit before an artifact exists.
        # Score it only when a valid terminal event proves that the model turn completed;
        # otherwise leave the run replaceable as infrastructure.
        if log_evidence and log_evidence["valid"] and log_evidence["turnCompleted"] \
                and not log_evidence["turnFailed"] and (
                    artifact_sampled or log_evidence["successfulCommands"] > 0
                ):
            return "CANDIDATE_FAILURE", "generation-exit"
        return "INFRA_FAILURE", "generation-unavailable"
    if log_evidence is not None and (
        not log_evidence["valid"]
        or not log_evidence["turnCompleted"]
        or log_evidence["turnFailed"]
    ):
        return "INFRA_FAILURE", "generation-inert"
    if (
        candidate is not None
        and candidate_hash(candidate) == pristine_candidate_hash()
        # candidate_hash covers only Cache.Core, so an agent that rewrote TASK.md or a public
        # test while leaving the implementation alone also hashes as pristine. That is the
        # model's doing; let evaluate_candidate record it as an integrity failure instead.
        and not candidate_integrity(candidate)
    ):
        if log_evidence is not None and log_evidence["successfulCommands"] > 0:
            # The model turn completed and still returned the starter, so a response was
            # sampled and the model gave up. That is a resolved candidate failure, not
            # something to retry, and treating it as infrastructure would censor it.
            return "CANDIDATE_FAILURE", "generation-unchanged"
        # Nothing ran and the implementation is byte-identical to the starter, so no artifact
        # was sampled. Scoring it would report the fixture's own failures as the model's, and
        # a generator sandbox or toolchain fault looks exactly like this.
        return "INFRA_FAILURE", "generation-inert"
    return None


def campaign_generation_failure_result(
    candidate: Path,
    job: dict[str, Any],
    *,
    backend: str,
    repeat: int,
    campaign_metadata: dict[str, Any],
    status: str,
    failure_kind: str,
) -> dict[str, Any]:
    report = {
        "schemaVersion": 1,
        "recordKind": "campaign",
        "runId": job["runId"],
        "fixtureId": FIXTURE_ID,
        "fixtureManifestHash": json_file_hash(MANIFEST),
        "candidateHash": candidate_hash(candidate),
        "promptHash": sha256_bytes(AGENT_PROMPT.encode("utf-8")),
        "model": job["model"],
        "reasoningEffort": job["reasoningEffort"],
        "machineId": job["machineId"],
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "provenance": repository_provenance(),
        "backend": backend,
        "evaluatorImage": docker_image_info(EVALUATOR_IMAGE) if backend == "docker" else None,
        "generatorImage": campaign_metadata["generatorImage"],
        "isolation": "container-strong",
        "repeatCount": repeat,
        "integrityViolations": [],
        "policyViolations": [],
        "manifestErrors": [],
        "tests": [],
        "status": status,
        "failureKind": failure_kind,
        # No evaluator was invoked, so generation latency must not be reported as
        # evaluation latency or included in evaluation-duration statistics.
        "durationSeconds": None,
        **campaign_metadata,
    }
    validate_result(report)
    return report


def load_matrix() -> list[dict[str, str]]:
    document = json.loads(MATRIX.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or set(document) != {"configurations"}:
        raise ValueError("Matrix must contain only configurations")
    configurations = document["configurations"]
    if not isinstance(configurations, list) or len(configurations) < 2 \
            or len(configurations) % 2 != 0:
        raise ValueError("Matrix must contain an even number of configurations")
    expected_keys = {"id", "model", "reasoningEffort"}
    for configuration in configurations:
        if not isinstance(configuration, dict) or set(configuration) != expected_keys:
            raise ValueError("Every matrix configuration must have the exact routing fields")
        if any(
            not isinstance(configuration[key], str) or not configuration[key].strip()
            for key in expected_keys
        ):
            raise ValueError("Matrix configuration values must be non-empty strings")
        if configuration["reasoningEffort"] not in {"low", "medium", "high"}:
            raise ValueError("Matrix contains an unsupported reasoning effort")
    identifiers = [configuration["id"] for configuration in configurations]
    treatments = [
        (configuration["model"], configuration["reasoningEffort"])
        for configuration in configurations
    ]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Matrix configuration IDs must be unique")
    if len(treatments) != len(set(treatments)):
        raise ValueError("Matrix model/effort treatments must be unique")
    return configurations


def seeded_order(values: list[Any], seed: str) -> list[Any]:
    def stable_token(value: Any) -> str:
        return canonical_json(value) if isinstance(value, (dict, list)) else str(value)

    return sorted(values, key=lambda value: sha256_bytes(f"{seed}:{stable_token(value)}".encode("utf-8")))


def williams_rows(configs: list[dict[str, str]], seed: str) -> list[list[dict[str, str]]]:
    permuted = seeded_order(configs, f"{seed}:configuration")
    count = len(permuted)
    if count % 2 != 0:
        raise ValueError("Williams design currently requires an even configuration count")
    indices = [0]
    for offset in range(1, count // 2 + 1):
        indices.append(offset)
        if count - offset != offset:
            indices.append(count - offset)
    base = indices[:count]
    return [[permuted[(index + row) % count] for index in base] for row in range(count)]


def schedule_jobs(trials: int, seed: str, machines: list[str]) -> list[dict[str, Any]]:
    configs = load_matrix()
    if not machines or len(set(machines)) != len(machines):
        raise ValueError("Machine labels must be non-empty and unique")
    balance_block = len(configs) * len(machines)
    if trials <= 0 or trials % balance_block != 0:
        raise ValueError(
            f"Trials must be a positive multiple of {balance_block} "
            "to balance every configuration across order positions and machines"
        )
    jobs: list[dict[str, Any]] = []
    trial = 0
    supercycles = trials // balance_block
    for cycle in range(supercycles):
        rows = williams_rows(configs, f"{seed}:cycle:{cycle}")
        row_order = seeded_order(list(range(len(rows))), f"{seed}:cycle:{cycle}:rows")
        machine_order = seeded_order(machines, f"{seed}:cycle:{cycle}:machines")
        for row_index in row_order:
            for machine in machine_order:
                for position, config in enumerate(rows[row_index]):
                    jobs.append({
                        "trial": trial,
                        "orderPosition": position,
                        "machineId": machine,
                        **config,
                    })
                trial += 1
    return jobs


def make_plan(trials: int, seed: str, machines: list[str], id_key: bytes) -> dict[str, Any]:
    scheduled = schedule_jobs(trials, seed, machines)
    jobs: list[dict[str, Any]] = []
    for job in scheduled:
        message = f"{seed}:{FIXTURE_ID}:{job['trial']}:{job['id']}".encode("utf-8")
        opaque = hmac.new(id_key, message, hashlib.sha256).hexdigest()[:16]
        jobs.append({"runId": opaque, **job})
    plan = {
        "schemaVersion": 1,
        "fixtureId": FIXTURE_ID,
        "seed": seed,
        "trials": trials,
        "machines": machines,
        "jobs": jobs,
    }
    validate_plan(plan)
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    required_keys = {"schemaVersion", "fixtureId", "seed", "trials", "machines", "jobs"}
    if not isinstance(plan, dict) or set(plan) != required_keys:
        raise ValueError("Plan must contain the exact protocol fields")
    if plan.get("schemaVersion") != 1 or plan.get("fixtureId") != FIXTURE_ID:
        raise ValueError("Unsupported plan identity")
    if not isinstance(plan["seed"], str) or not plan["seed"].strip():
        raise ValueError("Plan seed must be a non-empty string")
    configs = load_matrix()
    config_by_id = {configuration["id"]: configuration for configuration in configs}
    config_count = len(configs)
    machines = plan.get("machines")
    if not isinstance(machines, list) or not machines or any(
        not isinstance(machine, str) or not machine.strip() for machine in machines
    ) or len(machines) != len(set(machines)):
        raise ValueError("Plan machines must be non-empty, unique strings")
    trials = plan.get("trials")
    balance_block = config_count * len(machines)
    if not isinstance(trials, int) or isinstance(trials, bool) or trials <= 0 \
            or trials % balance_block != 0:
        raise ValueError(
            f"Plan trials must be a positive multiple of {balance_block}"
        )
    jobs = plan.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != trials * config_count:
        raise ValueError("Plan jobs do not cover every trial and configuration")
    run_ids: list[str] = []
    expected_job_keys = {
        "runId", "trial", "orderPosition", "machineId",
        "id", "model", "reasoningEffort",
    }
    for job in jobs:
        if not isinstance(job, dict) or set(job) != expected_job_keys:
            raise ValueError("Invalid plan job")
        if any(
            not isinstance(job[field], int) or isinstance(job[field], bool)
            for field in ("trial", "orderPosition")
        ):
            raise ValueError("Plan trial and order positions must be integers")
        run_ids.append(validate_run_id(job["runId"]))
        if job["machineId"] not in machines:
            raise ValueError("Plan job references an unknown machine")
        configuration = config_by_id.get(job["id"])
        if configuration is None or any(job[key] != configuration[key] for key in configuration):
            raise ValueError("Plan job does not match the current matrix")
    if len(run_ids) != len(set(run_ids)):
        raise ValueError("Plan contains duplicate run IDs")

    expected_schedule = schedule_jobs(trials, plan["seed"], machines)
    actual_schedule = [
        {key: value for key, value in job.items() if key != "runId"}
        for job in jobs
    ]
    if actual_schedule != expected_schedule:
        raise ValueError("Plan jobs do not match the seed-derived Williams schedule")

    # The physical order is part of the execution protocol: one contiguous trial block,
    # positions 0..N-1, all treatments exactly once. This prevents an edited plan from
    # preserving counts while silently changing queue order.
    trial_blocks: list[list[dict[str, Any]]] = []
    for trial in range(trials):
        block = jobs[trial * config_count:(trial + 1) * config_count]
        if any(job["trial"] != trial for job in block):
            raise ValueError("Plan trials must be contiguous and zero-based")
        if [job["orderPosition"] for job in block] != list(range(config_count)):
            raise ValueError("Plan order positions must be contiguous and zero-based")
        if len({job["machineId"] for job in block}) != 1:
            raise ValueError("Each plan trial must run on exactly one machine")
        if {job["id"] for job in block} != set(config_by_id):
            raise ValueError("Each plan trial must contain every matrix configuration")
        trial_blocks.append(block)

    cycles_per_machine = trials // balance_block
    trials_per_machine = trials // len(machines)
    for machine in machines:
        machine_blocks = [block for block in trial_blocks if block[0]["machineId"] == machine]
        if len(machine_blocks) != trials_per_machine:
            raise ValueError("Plan trials are not balanced across machines")
        position_counts: dict[tuple[str, int], int] = {}
        predecessor_counts: dict[tuple[str, str], int] = {}
        for block in machine_blocks:
            for job in block:
                key = (job["id"], job["orderPosition"])
                position_counts[key] = position_counts.get(key, 0) + 1
            for previous, current in zip(block, block[1:]):
                key = (previous["id"], current["id"])
                predecessor_counts[key] = predecessor_counts.get(key, 0) + 1
        if any(
            position_counts.get((configuration["id"], position), 0) != cycles_per_machine
            for configuration in configs
            for position in range(config_count)
        ):
            raise ValueError("Plan is not balanced by treatment, position, and machine")
        if any(
            predecessor_counts.get((left["id"], right["id"]), 0) != cycles_per_machine
            for left in configs
            for right in configs
            if left["id"] != right["id"]
        ):
            raise ValueError("Plan is not balanced for direct predecessor effects")


def wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return centre - margin, centre + margin


def required_samples_per_configuration(
    baseline_rate: float,
    target_rate: float,
    *,
    comparisons: int = 15,
    familywise_alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    if not 0 < baseline_rate < 1 or not 0 < target_rate < 1 \
            or baseline_rate == target_rate:
        raise ValueError("Pass rates must be distinct values strictly between zero and one")
    if comparisons <= 0 or not 0 < familywise_alpha < 1 or not 0 < power < 1:
        raise ValueError("Comparisons, alpha, and power must be positive and well-formed")
    adjusted_alpha = familywise_alpha / comparisons
    normal = statistics.NormalDist()
    z_alpha = normal.inv_cdf(1 - adjusted_alpha / 2)
    z_power = normal.inv_cdf(power)
    pooled = (baseline_rate + target_rate) / 2
    numerator = (
        z_alpha * math.sqrt(2 * pooled * (1 - pooled))
        + z_power * math.sqrt(
            baseline_rate * (1 - baseline_rate)
            + target_rate * (1 - target_rate)
        )
    ) ** 2
    return math.ceil(numerator / (target_rate - baseline_rate) ** 2)


def load_result_schema() -> dict[str, Any]:
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" \
            or schema.get("additionalProperties") is not False:
        raise ValueError("Result schema must be strict JSON Schema draft 2020-12")
    return schema


def load_evidence_schema() -> dict[str, Any]:
    schema = json.loads(EVIDENCE_SCHEMA.read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" \
            or schema.get("additionalProperties") is not False:
        raise ValueError("Evidence schema must be strict JSON Schema draft 2020-12")
    return schema


def validate_schema_instance(
    value: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> None:
    root = root_schema or schema
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/"):
            raise ValueError(f"Unsupported external schema reference at {path}: {reference}")
        target: Any = root
        for part in reference[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        validate_schema_instance(value, target, root_schema=root, path=path)
        return

    def matches(candidate: dict[str, Any]) -> bool:
        try:
            validate_schema_instance(value, candidate, root_schema=root, path=path)
            return True
        except ValueError:
            return False

    if "allOf" in schema:
        for candidate in schema["allOf"]:
            validate_schema_instance(value, candidate, root_schema=root, path=path)
    if "anyOf" in schema and not any(matches(candidate) for candidate in schema["anyOf"]):
        raise ValueError(f"Schema anyOf did not match at {path}")
    if "not" in schema and matches(schema["not"]):
        raise ValueError(f"Schema forbidden shape matched at {path}")
    if "if" in schema:
        branch = schema.get("then") if matches(schema["if"]) else schema.get("else")
        if branch is not None:
            validate_schema_instance(value, branch, root_schema=root, path=path)

    expected_types = schema.get("type")
    if expected_types is not None:
        names = [expected_types] if isinstance(expected_types, str) else expected_types

        def has_type(name: str) -> bool:
            return {
                "null": value is None,
                "object": isinstance(value, dict),
                "array": isinstance(value, list),
                "string": isinstance(value, str),
                "boolean": isinstance(value, bool),
                "integer": isinstance(value, int) and not isinstance(value, bool),
                "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            }.get(name, False)

        if not any(has_type(name) for name in names):
            raise ValueError(f"Schema type mismatch at {path}: expected {names}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"Schema const mismatch at {path}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"Schema enum mismatch at {path}: {value!r}")
    if isinstance(value, str):
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError(f"Schema pattern mismatch at {path}")
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"Schema string is too short at {path}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f"Schema number is not finite at {path}")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"Schema number is below minimum at {path}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"Schema number is above maximum at {path}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise ValueError(f"Schema array length mismatch at {path}")
        if schema.get("uniqueItems") and len({canonical_json(item) for item in value}) != len(value):
            raise ValueError(f"Schema array items are not unique at {path}")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_schema_instance(
                    item, schema["items"], root_schema=root, path=f"{path}[{index}]"
                )
    if isinstance(value, dict):
        required = set(schema.get("required", []))
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"Schema required fields missing at {path}: {missing}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate_schema_instance(
                    item, properties[key], root_schema=root, path=f"{path}.{key}"
                )
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"Schema additional property at {path}: {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema_instance(
                    item, schema["additionalProperties"], root_schema=root,
                    path=f"{path}.{key}",
                )


def validate_result(result: dict[str, Any]) -> None:
    required = {
        "schemaVersion", "recordKind", "runId", "fixtureId", "fixtureManifestHash", "candidateHash",
        "promptHash", "model", "reasoningEffort", "machineId", "status", "tests",
        "failureKind", "provenance", "repeatCount", "backend", "evaluatorImage",
        "generatorImage", "durationSeconds", "isolation", "integrityViolations",
        "policyViolations", "manifestErrors", "platform",
    }
    schema = load_result_schema()
    schema_required = set(schema.get("required", []))
    if schema_required != required:
        raise ValueError(
            "Result validator/schema required fields disagree: "
            f"validatorOnly={sorted(required - schema_required)}, "
            f"schemaOnly={sorted(schema_required - required)}"
        )
    # A kind the harness can emit but the schema rejects turns a classified failure into a
    # crash at save time, which is how `generation-inert` first shipped. Compare both ways.
    declared_kinds = INFRA_FAILURE_KINDS | CANDIDATE_FAILURE_KINDS | {None}
    schema_kinds = set(schema["properties"]["failureKind"]["enum"])
    if declared_kinds != schema_kinds:
        raise ValueError(
            "Result validator/schema failure kinds disagree: "
            f"validatorOnly={sorted(k for k in declared_kinds - schema_kinds if k)}, "
            f"schemaOnly={sorted(k for k in schema_kinds - declared_kinds if k)}"
        )
    validate_schema_instance(result, schema)
    unexpected_fields = sorted(set(result) - set(schema.get("properties", {})))
    if unexpected_fields:
        raise ValueError(f"Result contains fields outside the schema: {unexpected_fields}")
    missing = sorted(required - set(result))
    if missing:
        raise ValueError(f"Result is missing required fields: {missing}")
    if result["recordKind"] not in set(schema["properties"]["recordKind"]["enum"]):
        raise ValueError(f"Invalid record kind: {result['recordKind']!r}")
    if result["recordKind"] == "campaign":
        campaign_required = {
            "planHash", "trial", "orderPosition", "generationDurationSeconds",
        }
        campaign_missing = sorted(campaign_required - set(result))
        if campaign_missing:
            raise ValueError(f"Campaign result is missing required fields: {campaign_missing}")
        if not re.fullmatch(r"[0-9a-f]{64}", result["planHash"]):
            raise ValueError(f"Invalid plan hash for {result['runId']}")
        if any(
            not isinstance(result[field], int) or isinstance(result[field], bool) or result[field] < 0
            for field in ("trial", "orderPosition")
        ):
            raise ValueError(f"Invalid campaign position for {result['runId']}")
        if not isinstance(result["generationDurationSeconds"], (int, float)) \
                or result["generationDurationSeconds"] < 0:
            raise ValueError(f"Invalid generation duration for {result['runId']}")
        if "queueAudit" in result:
            queue_audit = result["queueAudit"]
            if not isinstance(queue_audit, dict) or set(queue_audit) != {"continuedAfterUnresolvedRunIds"}:
                raise ValueError(f"Invalid queue audit for {result['runId']}")
            continued = queue_audit["continuedAfterUnresolvedRunIds"]
            if not isinstance(continued, list) or not continued:
                raise ValueError(f"Invalid queue audit for {result['runId']}")
            for predecessor_id in continued:
                validate_run_id(predecessor_id)
        if "replacementAudit" in result:
            replacement = result["replacementAudit"]
            if not isinstance(replacement, dict) or set(replacement) != {
                "previousResultHash", "previousStatus", "reason", "archivedResult",
            }:
                raise ValueError(f"Invalid replacement audit for {result['runId']}")
            if not re.fullmatch(r"[0-9a-f]{64}", replacement["previousResultHash"]):
                raise ValueError(f"Invalid replacement audit hash for {result['runId']}")
            if replacement["previousStatus"] != "INFRA_FAILURE":
                raise ValueError(f"Invalid replacement audit status for {result['runId']}")
            if not isinstance(replacement["reason"], str) or not replacement["reason"].strip():
                raise ValueError(f"Invalid replacement audit reason for {result['runId']}")
            expected_archive = (
                f"runs/replacements/{result['runId']}/{replacement['previousResultHash']}.json"
            )
            if replacement["archivedResult"] != expected_archive:
                raise ValueError(f"Invalid replacement archive for {result['runId']}")
    else:
        campaign_only = {
            "planHash", "trial", "orderPosition", "generationDurationSeconds",
            "queueAudit", "replacementAudit",
        }
        unexpected = sorted(campaign_only & set(result))
        if unexpected:
            raise ValueError(f"External result contains campaign fields: {unexpected}")
    validate_run_id(
        result["runId"],
        allow_verify=result["recordKind"] == "external-evaluation"
        and result.get("isolation") == "fixture-verification",
    )
    if result["schemaVersion"] != 1 or result["fixtureId"] != FIXTURE_ID:
        raise ValueError(f"Unsupported result identity for {result['runId']}")
    if result["status"] not in set(schema["properties"]["status"]["enum"]):
        raise ValueError(f"Invalid status for {result['runId']}: {result['status']}")
    if result["failureKind"] not in set(schema["properties"]["failureKind"]["enum"]):
        raise ValueError(f"Invalid failureKind for {result['runId']}: {result['failureKind']}")
    if result["status"] == "PASS" and result["failureKind"] is not None:
        raise ValueError(f"PASS result has failureKind for {result['runId']}")
    if result["status"] != "PASS" and not isinstance(result["failureKind"], str):
        raise ValueError(f"Failed result lacks failureKind for {result['runId']}")
    if result["status"] == "CANDIDATE_FAILURE" and result["failureKind"] not in CANDIDATE_FAILURE_KINDS:
        raise ValueError(f"Candidate failureKind has incorrect status for {result['runId']}")
    if result["status"] == "INFRA_FAILURE" and result["failureKind"] not in INFRA_FAILURE_KINDS:
        raise ValueError(f"Infrastructure failureKind has incorrect status for {result['runId']}")
    generation_only = (
        result["recordKind"] == "campaign"
        and isinstance(result["failureKind"], str)
        and result["failureKind"].startswith("generation-")
    )
    if generation_only:
        if result["tests"] or result["durationSeconds"] is not None:
            raise ValueError(
                f"Generation-only result contains evaluation evidence for {result['runId']}"
            )
    elif not isinstance(result["durationSeconds"], (int, float)) \
            or isinstance(result["durationSeconds"], bool):
        raise ValueError(f"Evaluated result lacks evaluation duration for {result['runId']}")
    if not isinstance(result["tests"], list):
        raise ValueError(f"Invalid tests collection for {result['runId']}")
    if result["status"] == "PASS":
        names = [test.get("name") for test in result["tests"] if isinstance(test, dict)]
        required_names = {"public-build", "public", "hidden-build"} | {
            f"hidden-{index}" for index in range(1, result.get("repeatCount", 0) + 1)
        }
        if (
            result.get("repeatCount", 0) <= 0
            or len(names) != len(set(names))
            or set(names) != required_names
        ):
            raise ValueError(f"PASS result lacks complete test evidence for {result['runId']}")
    for test in result["tests"]:
        if not isinstance(test, dict) or not {
            "name", "attempts", "hadTimeout", "confirmedTimeout"
        }.issubset(test):
            raise ValueError(f"Invalid test evidence for {result['runId']}")
        test_schema = schema["$defs"]["testEvidence"]
        if set(test) - set(test_schema["properties"]):
            raise ValueError(f"Test evidence contains fields outside the schema for {result['runId']}")
        if not isinstance(test["attempts"], list) or not 1 <= len(test["attempts"]) <= 2:
            raise ValueError(f"Invalid attempts for {result['runId']}")
        for attempt in test["attempts"]:
            required_attempt = {
                "command", "exitCode", "stdout", "stderr", "timedOut",
                "launcherFailure", "outputLimitExceeded", "durationSeconds",
            }
            attempt_schema = schema["$defs"]["attempt"]
            if not isinstance(attempt, dict) or set(attempt) != required_attempt \
                    or set(attempt) - set(attempt_schema["properties"]):
                raise ValueError(f"Invalid attempt evidence for {result['runId']}")
        if bool(test["hadTimeout"]) != any(attempt["timedOut"] for attempt in test["attempts"]):
            raise ValueError(f"Inconsistent timeout evidence for {result['runId']}")
        if bool(test["confirmedTimeout"]) != bool(test["attempts"][-1]["timedOut"]):
            raise ValueError(f"Inconsistent confirmed timeout for {result['runId']}")
        if test["name"].startswith("hidden-") and test["name"] != "hidden-build":
            behaviors = test.get("behaviors")
            if not isinstance(behaviors, list):
                raise ValueError(f"Hidden test lacks behavior outcomes for {result['runId']}")
            names = [behavior.get("name") for behavior in behaviors if isinstance(behavior, dict)]
            if names != list(HIDDEN_BEHAVIORS):
                raise ValueError(f"Invalid behavior outcomes for {result['runId']}")
            for behavior in behaviors:
                if set(behavior) != {"name", "outcome"} \
                        or behavior["name"] not in HIDDEN_BEHAVIORS \
                        or behavior["outcome"] not in {"PASS", "FAIL", "NOT_RUN", "AMBIGUOUS"}:
                    raise ValueError(f"Invalid behavior outcome for {result['runId']}")
        if result["status"] == "PASS":
            final_attempt = test["attempts"][-1]
            if (
                test["hadTimeout"] or test["confirmedTimeout"]
                or final_attempt["exitCode"] != 0
                or final_attempt["timedOut"]
                or final_attempt["launcherFailure"]
                or final_attempt["outputLimitExceeded"]
            ):
                raise ValueError(f"PASS result contains failed evidence for {result['runId']}")
            if test["name"] == "public" and final_attempt["stdout"].count(PUBLIC_PASS_MARKER) != 1:
                raise ValueError(f"PASS result lacks public completion marker for {result['runId']}")
            if test["name"].startswith("hidden-") and test["name"] != "hidden-build":
                if final_attempt["stdout"].count(HIDDEN_PASS_MARKER) != 1:
                    raise ValueError(f"PASS result lacks hidden completion marker for {result['runId']}")
                if test["behaviors"] != [
                    {"name": behavior, "outcome": "PASS"} for behavior in HIDDEN_BEHAVIORS
                ]:
                    raise ValueError(f"PASS result lacks complete behavior evidence for {result['runId']}")


def quartiles(values: list[float]) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        return [values[0], values[0]]
    cuts = statistics.quantiles(values, n=4, method="inclusive")
    return [cuts[0], cuts[2]]


def validate_campaign_environment(
    result: dict[str, Any], expected_provenance: dict[str, Any]
) -> None:
    """Reject stale or heterogeneous evaluator/generator environments.

    Image IDs may differ across CPU architectures, but tags and build-input spec hashes
    are protocol inputs and must match the checked-out harness exactly.
    """
    run_id = result["runId"]
    if result["provenance"] != expected_provenance:
        raise ValueError(f"Repository provenance mismatch for {run_id}")
    if result["backend"] != "docker" or result["isolation"] != "container-strong":
        raise ValueError(f"Campaign isolation mismatch for {run_id}")
    expected_images = {
        "evaluatorImage": (EVALUATOR_IMAGE, image_spec_hash("docker/evaluator.Dockerfile")),
        "generatorImage": (GENERATOR_IMAGE, image_spec_hash("docker/generator.Dockerfile")),
    }
    for field, (tag, spec_hash) in expected_images.items():
        image = result[field]
        if not isinstance(image, dict) or image.get("tag") != tag \
                or image.get("specHash") != spec_hash or image.get("os") != "linux" \
                or not isinstance(image.get("id"), str) or not image["id"]:
            raise ValueError(f"Campaign {field} mismatch for {run_id}")


def aggregate(
    paths: list[Path], plan_path: Path, *, allow_incomplete: bool = False
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan)
    plan_hash = json_value_hash(plan)
    planned = {job["runId"]: job for job in plan["jobs"]}
    seen: set[str] = set()
    results_by_id: dict[str, dict[str, Any]] = {}
    expected_provenance = repository_provenance()
    cohort_repeat: int | None = None
    machine_images: dict[str, str] = {}
    for path in paths:
        result = json.loads(path.read_text(encoding="utf-8"))
        validate_result(result)
        if result["recordKind"] != "campaign":
            raise ValueError(f"Aggregation rejects {result['recordKind']} record {result['runId']}")
        run_id = result["runId"]
        if run_id in seen:
            raise ValueError(f"Duplicate run ID: {run_id}")
        seen.add(run_id)
        if run_id not in planned:
            raise ValueError(f"Unplanned result: {run_id}")
        job = planned[run_id]
        expected = {
            "model": job["model"], "reasoningEffort": job["reasoningEffort"],
            "machineId": job["machineId"], "trial": job["trial"],
            "orderPosition": job["orderPosition"], "planHash": plan_hash,
        }
        for field, value in expected.items():
            if result[field] != value:
                raise ValueError(f"Plan mismatch for {run_id}: {field}")
        if result["fixtureManifestHash"] != json_file_hash(MANIFEST):
            raise ValueError(f"Fixture mismatch for {run_id}")
        if result["promptHash"] != sha256_bytes(AGENT_PROMPT.encode("utf-8")):
            raise ValueError(f"Prompt mismatch for {run_id}")
        validate_campaign_environment(result, expected_provenance)
        if cohort_repeat is None:
            cohort_repeat = result["repeatCount"]
        elif result["repeatCount"] != cohort_repeat:
            raise ValueError(f"Mixed repeat counts in cohort at {run_id}")
        full_image_identity = canonical_json({
            "evaluatorImage": result["evaluatorImage"],
            "generatorImage": result["generatorImage"],
        })
        previous_identity = machine_images.setdefault(result["machineId"], full_image_identity)
        if previous_identity != full_image_identity:
            raise ValueError(f"Image identity changed within machine {result['machineId']}")
        results_by_id[run_id] = result

    missing_runs = sorted(set(planned) - seen)
    unresolved = sorted(
        result["runId"] for result in results_by_id.values()
        if result["status"] == "INFRA_FAILURE"
    )
    if not allow_incomplete and (missing_runs or unresolved):
        raise ValueError(
            f"Official aggregation requires a complete resolved cohort; "
            f"missing={len(missing_runs)}, unresolved={len(unresolved)}"
        )

    candidate_hash_counts: dict[str, int] = {}
    for result in results_by_id.values():
        candidate_hash_counts[result["candidateHash"]] = candidate_hash_counts.get(result["candidateHash"], 0) + 1
    duplicates = {
        candidate_hash: count
        for candidate_hash, count in candidate_hash_counts.items()
        if candidate_hash is not None and count > 1
    }

    summary: dict[str, Any] = {
        "schemaVersion": 1,
        "planHash": plan_hash,
        "complete": not missing_runs and not unresolved,
        "missingRunIds": missing_runs,
        "unresolvedRunIds": unresolved,
        "duplicateCandidateHashes": duplicates,
        "groups": {},
    }
    group_keys = sorted({f"{job['model']}:{job['reasoningEffort']}" for job in plan["jobs"]})
    for key in group_keys:
        planned_jobs = [
            job for job in plan["jobs"]
            if f"{job['model']}:{job['reasoningEffort']}" == key
        ]
        results = [results_by_id[job["runId"]] for job in planned_jobs if job["runId"] in results_by_id]
        resolved = [result for result in results if result["status"] != "INFRA_FAILURE"]
        successes = sum(result["status"] == "PASS" for result in results)
        denominator = len(planned_jobs)
        low, high = wilson(successes, denominator)
        durations = [
            result["durationSeconds"] for result in resolved
            if isinstance(result["durationSeconds"], (int, float))
            and not isinstance(result["durationSeconds"], bool)
        ]
        generation_durations = [result["generationDurationSeconds"] for result in resolved]
        machines = sorted({job["machineId"] for job in planned_jobs})
        summary["groups"][key] = {
            "plannedRuns": denominator,
            "observedRuns": len(results),
            "totalRuns": len(results),
            "eligibleRuns": len(resolved),
            "passes": successes,
            "passRate": successes / denominator if denominator else None,
            "wilson95": [low, high] if denominator else None,
            "observedResolvedPassRate": successes / len(resolved) if resolved else None,
            "medianEvaluationSeconds": statistics.median(durations) if durations else None,
            "evaluationIqrSeconds": quartiles(durations),
            "medianGenerationSeconds": statistics.median(generation_durations) if generation_durations else None,
            "generationIqrSeconds": quartiles(generation_durations),
            "byMachine": {
                machine: {
                    "runs": sum(job["machineId"] == machine for job in planned_jobs),
                    "observed": sum(result["machineId"] == machine for result in results),
                    "resolved": sum(result["machineId"] == machine for result in resolved),
                    "passes": sum(
                        result["machineId"] == machine and result["status"] == "PASS"
                        for result in results
                    ),
                }
                for machine in machines
            },
            "statuses": {
                status: sum(result["status"] == status for result in results)
                for status in sorted({result["status"] for result in results})
            },
            "behaviors": behavior_summary(results, denominator),
            "failureKinds": {
                kind: sum(result["failureKind"] == kind for result in results)
                for kind in sorted({
                    result["failureKind"] for result in results
                    if result["failureKind"] is not None
                })
            },
        }
    return summary


def verify_fixture(repeat: int, timeout: int, backend: str) -> dict[str, Any]:
    if repeat <= 0 or timeout <= 0:
        raise ValueError("repeat and timeout must be positive integers")
    if backend == "docker":
        ensure_image(EVALUATOR_IMAGE, "docker/evaluator.Dockerfile")
    outcomes: list[dict[str, Any]] = []
    cases = [("reference", REFERENCE, "PASS")]
    cases.extend((path.stem, path, "CANDIDATE_FAILURE") for path in sorted((FIXTURE / "mutants").glob("*.cs")))
    with tempfile.TemporaryDirectory(prefix="codex-bench-verify-") as temp_name:
        for name, source, expected in cases:
            candidate = Path(temp_name) / name
            materialize(candidate)
            shutil.copy2(source, candidate / "Cache.Core" / "AsyncExpiringCache.cs")
            result = evaluate_candidate(
                candidate,
                run_id=f"verify-{name}",
                model="fixture",
                effort="none",
                machine=platform.node() or "local",
                repeat=repeat,
                timeout=timeout,
                backend=backend,
                isolation="fixture-verification",
                trusted=True,
            )
            outcomes.append({"case": name, "expected": expected, "actual": result["status"], "result": result})
    passed = all(outcome["expected"] == outcome["actual"] for outcome in outcomes)
    return {"schemaVersion": 1, "passed": passed, "backend": backend, "repeat": repeat, "outcomes": outcomes}


def sanitized_result_for_publish(result: dict[str, Any]) -> dict[str, Any]:
    published = {
        key: result[key]
        for key in (
            "runId", "candidateHash", "status", "failureKind", "durationSeconds",
            "fixtureManifestHash", "promptHash",
            "generationDurationSeconds", "repeatCount", "backend", "isolation",
            "integrityViolations", "policyViolations", "manifestErrors", "platform",
            "provenance", "evaluatorImage", "generatorImage",
        )
    }
    published_tests: list[dict[str, Any]] = []
    for test in result["tests"]:
        published_test = {key: value for key, value in test.items() if key != "attempts"}
        published_attempts: list[dict[str, Any]] = []
        for attempt in test["attempts"]:
            sanitized = {
                key: value for key, value in attempt.items() if key not in {"stdout", "stderr"}
            }
            for stream_name in ("stdout", "stderr"):
                encoded = attempt[stream_name].encode("utf-8")
                sanitized[f"{stream_name}Bytes"] = len(encoded)
                sanitized[f"{stream_name}Sha256"] = sha256_bytes(encoded)
            published_attempts.append(sanitized)
        published_test["attempts"] = published_attempts
        published_tests.append(published_test)
    published["tests"] = published_tests
    return published


def validate_evidence_bundle(bundle: dict[str, Any]) -> None:
    validate_schema_instance(bundle, load_evidence_schema())
    protocol = bundle["protocol"]
    plan = protocol["plan"]
    validate_plan(plan)
    if protocol["matrix"] != load_matrix():
        raise ValueError("Evidence matrix does not match the validated plan matrix")
    fixture_manifest = protocol["fixtureManifest"]
    if not (
        bundle["fixtureId"] == plan["fixtureId"]
        == fixture_manifest["fixtureId"] == FIXTURE_ID
    ):
        raise ValueError("Evidence fixture identities disagree")
    expected_mapping = [
        {
            key: job[key]
            for key in (
                "runId", "model", "reasoningEffort", "machineId", "trial", "orderPosition"
            )
        }
        for job in plan["jobs"]
    ]
    if bundle["mapping"] != expected_mapping:
        raise ValueError("Evidence mapping does not match the embedded plan")
    planned_ids = {job["runId"] for job in plan["jobs"]}
    outcome_ids = [outcome["runId"] for outcome in bundle["outcomes"]]
    if len(outcome_ids) != len(set(outcome_ids)) or set(outcome_ids) != planned_ids:
        raise ValueError("Evidence outcomes do not form the planned cohort")
    if set(bundle["audit"]["sourceResultHashes"]) != planned_ids:
        raise ValueError("Evidence source-result hashes do not cover the planned cohort")
    if bundle["aggregate"]["planHash"] != bundle["audit"]["planHash"]:
        raise ValueError("Evidence aggregate and audit plan hashes disagree")
    if bundle["audit"]["planHash"] != json_value_hash(plan):
        raise ValueError("Evidence plan hash is not reproducible from the embedded plan")
    if bundle["audit"]["fixtureManifestHash"] != json_value_hash(fixture_manifest):
        raise ValueError("Evidence manifest hash is not reproducible from the embedded manifest")
    expected_aggregate_hash = sha256_bytes(
        canonical_json(bundle["aggregate"]).encode("utf-8")
    )
    if bundle["audit"]["aggregateHash"] != expected_aggregate_hash:
        raise ValueError("Evidence aggregate hash is invalid")
    outcomes_by_id = {outcome["runId"]: outcome for outcome in bundle["outcomes"]}
    mapping_by_id = {item["runId"]: item for item in bundle["mapping"]}
    repeat_counts = {outcome["repeatCount"] for outcome in outcomes_by_id.values()}
    if len(repeat_counts) != 1:
        raise ValueError("Evidence outcomes mix repeat counts")
    expected_image_protocol = {
        "evaluatorImage": (
            EVALUATOR_IMAGE,
            image_spec_hash_from_provenance(
                bundle["provenance"], "docker/evaluator.Dockerfile"
            ),
        ),
        "generatorImage": (
            GENERATOR_IMAGE,
            image_spec_hash_from_provenance(
                bundle["provenance"], "docker/generator.Dockerfile"
            ),
        ),
    }
    machine_images: dict[str, str] = {}
    for outcome in outcomes_by_id.values():
        if (outcome["status"] == "PASS") != (outcome["failureKind"] is None):
            raise ValueError(f"Evidence status/failureKind mismatch for {outcome['runId']}")
        if outcome["status"] == "CANDIDATE_FAILURE" \
                and outcome["failureKind"] not in CANDIDATE_FAILURE_KINDS:
            raise ValueError(f"Evidence failureKind is invalid for {outcome['runId']}")
        generation_only = isinstance(outcome["failureKind"], str) \
            and outcome["failureKind"].startswith("generation-")
        if (outcome["durationSeconds"] is None) != generation_only:
            raise ValueError(f"Evidence evaluation duration is invalid for {outcome['runId']}")
        if outcome["fixtureManifestHash"] != bundle["audit"]["fixtureManifestHash"]:
            raise ValueError(f"Evidence manifest link mismatch for {outcome['runId']}")
        if outcome["promptHash"] != bundle["audit"]["promptHash"]:
            raise ValueError(f"Evidence prompt link mismatch for {outcome['runId']}")
        if outcome["provenance"] != bundle["provenance"]:
            raise ValueError(f"Evidence provenance mismatch for {outcome['runId']}")
        if outcome["backend"] != "docker" or outcome["isolation"] != "container-strong":
            raise ValueError(f"Evidence isolation mismatch for {outcome['runId']}")
        for field, (tag, spec_hash) in expected_image_protocol.items():
            image = outcome[field]
            if not isinstance(image, dict) or image.get("tag") != tag \
                    or image.get("specHash") != spec_hash or image.get("os") != "linux":
                raise ValueError(f"Evidence {field} mismatch for {outcome['runId']}")
        machine = mapping_by_id[outcome["runId"]]["machineId"]
        identity = canonical_json({
            "evaluatorImage": outcome["evaluatorImage"],
            "generatorImage": outcome["generatorImage"],
        })
        previous_identity = machine_images.setdefault(machine, identity)
        if previous_identity != identity:
            raise ValueError(f"Evidence image identity changed within machine {machine}")
    hash_counts: dict[str, int] = {}
    for outcome in outcomes_by_id.values():
        candidate_hash_value = outcome["candidateHash"]
        hash_counts[candidate_hash_value] = hash_counts.get(candidate_hash_value, 0) + 1
    expected_summary: dict[str, Any] = {
        "schemaVersion": 1,
        "planHash": bundle["audit"]["planHash"],
        "complete": True,
        "missingRunIds": [],
        "unresolvedRunIds": [],
        "duplicateCandidateHashes": {
            candidate_hash_value: count
            for candidate_hash_value, count in hash_counts.items()
            if candidate_hash_value is not None and count > 1
        },
        "groups": {},
    }
    group_keys = sorted({
        f"{job['model']}:{job['reasoningEffort']}" for job in plan["jobs"]
    })
    for key in group_keys:
        planned_jobs = [
            job for job in plan["jobs"]
            if f"{job['model']}:{job['reasoningEffort']}" == key
        ]
        outcomes = [outcomes_by_id[job["runId"]] for job in planned_jobs]
        passes = sum(outcome["status"] == "PASS" for outcome in outcomes)
        denominator = len(planned_jobs)
        low, high = wilson(passes, denominator)
        evaluation_durations = [
            outcome["durationSeconds"] for outcome in outcomes
            if isinstance(outcome["durationSeconds"], (int, float))
            and not isinstance(outcome["durationSeconds"], bool)
        ]
        generation_durations = [outcome["generationDurationSeconds"] for outcome in outcomes]
        machines = sorted({job["machineId"] for job in planned_jobs})
        expected_summary["groups"][key] = {
            "plannedRuns": denominator,
            "observedRuns": denominator,
            "totalRuns": denominator,
            "eligibleRuns": denominator,
            "passes": passes,
            "passRate": passes / denominator,
            "wilson95": [low, high],
            "observedResolvedPassRate": passes / denominator,
            "medianEvaluationSeconds": (
                statistics.median(evaluation_durations) if evaluation_durations else None
            ),
            "evaluationIqrSeconds": quartiles(evaluation_durations),
            "medianGenerationSeconds": statistics.median(generation_durations),
            "generationIqrSeconds": quartiles(generation_durations),
            "byMachine": {
                machine: {
                    "runs": sum(job["machineId"] == machine for job in planned_jobs),
                    "observed": sum(
                        job["machineId"] == machine for job in planned_jobs
                    ),
                    "resolved": sum(
                        job["machineId"] == machine for job in planned_jobs
                    ),
                    "passes": sum(
                        job["machineId"] == machine
                        and outcomes_by_id[job["runId"]]["status"] == "PASS"
                        for job in planned_jobs
                    ),
                }
                for machine in machines
            },
            "behaviors": behavior_summary(outcomes, denominator),
            "statuses": {
                status: sum(outcome["status"] == status for outcome in outcomes)
                for status in sorted({outcome["status"] for outcome in outcomes})
            },
            "failureKinds": {
                kind: sum(outcome["failureKind"] == kind for outcome in outcomes)
                for kind in sorted({
                    outcome["failureKind"] for outcome in outcomes
                    if outcome["failureKind"] is not None
                })
            },
        }
    if bundle["aggregate"] != expected_summary:
        raise ValueError("Evidence aggregate does not match published outcomes")


def publish_evidence_bundle(paths: list[Path], plan_path: Path) -> dict[str, Any]:
    initial_hashes = {path: sha256_file(path) for path in paths}
    initial_plan = plan_path.read_bytes()
    initial_manifest = MANIFEST.read_bytes()
    summary = aggregate(paths, plan_path, allow_incomplete=False)
    if plan_path.read_bytes() != initial_plan or MANIFEST.read_bytes() != initial_manifest:
        raise ValueError("Plan or fixture manifest changed while publishing")
    plan = json.loads(initial_plan.decode("utf-8"))
    fixture_manifest = json.loads(initial_manifest.decode("utf-8"))
    validate_plan(plan)
    results: list[dict[str, Any]] = []
    source_hashes: dict[str, str] = {}
    audit_records: list[dict[str, Any]] = []
    for path in paths:
        raw = path.read_bytes()
        if sha256_bytes(raw) != initial_hashes[path]:
            raise ValueError(f"Source result changed while publishing: {path}")
        result = json.loads(raw.decode("utf-8"))
        validate_result(result)
        if result["recordKind"] != "campaign":
            raise ValueError(f"Publishing rejects {result['recordKind']} record {result['runId']}")
        results.append(sanitized_result_for_publish(result))
        source_hashes[result["runId"]] = initial_hashes[path]
        audit = {
            key: result[key]
            for key in ("queueAudit", "replacementAudit")
            if key in result
        }
        if audit:
            audit_records.append({"runId": result["runId"], **audit})
    results.sort(key=lambda result: result["runId"])
    mapping = [
        {
            key: job[key]
            for key in (
                "runId", "model", "reasoningEffort", "machineId", "trial", "orderPosition"
            )
        }
        for job in plan["jobs"]
    ]
    bundle = {
        "schemaVersion": 1,
        "recordKind": "published-evidence",
        "fixtureId": FIXTURE_ID,
        "protocol": {
            "plan": plan,
            "fixtureManifest": fixture_manifest,
            "matrix": load_matrix(),
        },
        "outcomes": results,
        "aggregate": summary,
        "mapping": mapping,
        "provenance": results[0]["provenance"],
        "audit": {
            "planHash": json_value_hash(plan),
            "fixtureManifestHash": json_value_hash(fixture_manifest),
            "promptHash": sha256_bytes(AGENT_PROMPT.encode("utf-8")),
            "sourceResultHashes": dict(sorted(source_hashes.items())),
            "aggregateHash": sha256_bytes(canonical_json(summary).encode("utf-8")),
            "records": audit_records,
        },
    }
    validate_evidence_bundle(bundle)
    return bundle


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--write", action="store_true")

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--destination", type=Path, required=True)

    build = subparsers.add_parser("build-images")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--repeat", type=positive_int, default=20)
    verify.add_argument(
        "--timeout", type=positive_int, default=DEFAULT_EVALUATION_TIMEOUT_SECONDS
    )
    verify.add_argument("--backend", choices=["native", "docker"], default="native")
    verify.add_argument("--output", type=Path, default=RUNS / "verification.json")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--candidate", type=Path, required=True)
    evaluate.add_argument("--run-id", required=True)
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--effort", required=True)
    evaluate.add_argument("--machine", default=platform.node() or "local")
    evaluate.add_argument("--repeat", type=positive_int, default=20)
    evaluate.add_argument(
        "--timeout", type=positive_int, default=DEFAULT_EVALUATION_TIMEOUT_SECONDS
    )
    evaluate.add_argument("--backend", choices=["docker"], default="docker")
    evaluate.add_argument("--isolation", default="external-candidate")
    evaluate.add_argument("--output", type=Path)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--trials", type=int, default=24)
    plan.add_argument("--seed", default="20260817")
    plan.add_argument("--machines", default="machine-a")
    plan.add_argument("--output", type=Path, default=RUNS / "plan.json")
    plan.add_argument("--blinded-output", type=Path, default=RUNS / "plan.blinded.json")
    plan.add_argument("--id-key-file", type=Path, default=RUNS / "id-key")

    job = subparsers.add_parser("run-job")
    job.add_argument("--plan", type=Path, default=RUNS / "plan.json")
    job.add_argument("--run-id", required=True)
    job.add_argument("--repeat", type=positive_int, default=20)
    job.add_argument(
        "--timeout", type=positive_int, default=DEFAULT_EVALUATION_TIMEOUT_SECONDS
    )
    job.add_argument("--backend", choices=["docker"], default="docker")
    job.add_argument("--replace", action="store_true")
    job.add_argument("--replacement-reason", default="operator-requested")
    job.add_argument("--continue-after-unresolved", action="store_true")
    job.add_argument("--auth-file", type=Path)
    job.add_argument("--machine-id", default=platform.node() or "local")

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("paths", nargs="+", type=Path)
    aggregate_parser.add_argument("--plan", type=Path, default=RUNS / "plan.json")
    aggregate_parser.add_argument("--allow-incomplete", action="store_true")
    aggregate_parser.add_argument("--output", type=Path, default=RUNS / "summary.json")

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("paths", nargs="+", type=Path)
    publish_parser.add_argument("--plan", type=Path, default=RUNS / "plan.json")
    publish_parser.add_argument("--output", type=Path, default=RUNS / "evidence.json")

    generator_check = subparsers.add_parser("check-generator")
    generator_check.add_argument("--timeout", type=positive_int, default=600)
    generator_check.add_argument("--output", type=Path, default=RUNS / "generator-check.json")

    power_parser = subparsers.add_parser("power")
    power_parser.add_argument("--baseline-rate", type=float, required=True)
    power_parser.add_argument("--target-rate", type=float, required=True)
    power_parser.add_argument("--comparisons", type=positive_int, default=15)
    power_parser.add_argument("--familywise-alpha", type=float, default=0.05)
    power_parser.add_argument("--power", type=float, default=0.80)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "manifest":
        if args.write:
            payload = write_manifest()
            print(f"Wrote manifest with {len(payload['files'])} files")
            return 0
        valid, errors = verify_manifest()
        if valid:
            print("Fixture manifest is valid")
            return 0
        print("\n".join(errors), file=sys.stderr)
        return 1

    if args.command == "prepare":
        materialize(args.destination)
        print(args.destination.resolve())
        return 0

    if args.command == "build-images":
        build_images()
        return 0

    if args.command == "verify":
        report = verify_fixture(args.repeat, args.timeout, args.backend)
        save_json(args.output, report, private=True)
        print(json.dumps({"passed": report["passed"], "output": str(args.output)}, indent=2))
        return 0 if report["passed"] else 1

    if args.command == "evaluate":
        validate_run_id(args.run_id)
        output = args.output or run_artifact_path(RUNS / "external-results", args.run_id, ".json")
        campaign_results = (RUNS / "results").resolve()
        resolved_output = Path(os.path.abspath(output)).resolve()
        if resolved_output == campaign_results or campaign_results in resolved_output.parents:
            raise ValueError("External evaluations cannot write into the campaign results directory")
        if output.exists():
            raise FileExistsError(f"External evaluation result already exists: {output}")
        if args.backend == "docker":
            ensure_image(EVALUATOR_IMAGE, "docker/evaluator.Dockerfile")
        report = evaluate_candidate(
            Path(os.path.abspath(args.candidate)), run_id=args.run_id, model=args.model, effort=args.effort,
            machine=args.machine, repeat=args.repeat, timeout=args.timeout, backend=args.backend,
            isolation=args.isolation,
        )
        validate_result(report)
        save_json(output, report, private=True, replace=False)
        print(json.dumps({"status": report["status"], "output": str(output)}, indent=2))
        return 0 if report["status"] == "PASS" else 1

    if args.command == "plan":
        machines = [machine.strip() for machine in args.machines.split(",") if machine.strip()]
        if not machines:
            raise ValueError("At least one machine is required")
        payload = make_plan(args.trials, args.seed, machines, load_or_create_id_key(args.id_key_file))
        save_json(args.output, payload, private=True)
        blinded = {
            "schemaVersion": payload["schemaVersion"],
            "fixtureId": payload["fixtureId"],
            "jobs": [
                {key: job[key] for key in ["runId", "trial", "orderPosition", "machineId"]}
                for job in payload["jobs"]
            ],
        }
        save_json(args.blinded_output, blinded, private=True)
        print(json.dumps({
            "jobs": len(payload["jobs"]),
            "output": str(args.output),
            "planHash": json_value_hash(payload),
            "blindedOutput": str(args.blinded_output),
            "blindedPlanHash": sha256_file(args.blinded_output),
        }, indent=2))
        return 0

    if args.command == "run-job":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        validate_plan(plan)
        validate_run_id(args.run_id)
        matches = [job for job in plan["jobs"] if job["runId"] == args.run_id]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one job for {args.run_id}; found {len(matches)}")
        job = matches[0]
        validate_run_id(job["runId"])
        if job["machineId"] != args.machine_id:
            raise ValueError(
                f"Plan assigns {job['runId']} to {job['machineId']}, not {args.machine_id}"
            )
        job_index = plan["jobs"].index(job)
        missing_predecessors: list[str] = []
        unresolved_predecessors: list[str] = []
        current_plan_hash = json_value_hash(plan)
        for prior in plan["jobs"][:job_index]:
            if prior["machineId"] != args.machine_id:
                continue
            predecessor_path = run_artifact_path(RUNS / "results", prior["runId"], ".json")
            if not predecessor_path.exists():
                missing_predecessors.append(prior["runId"])
                continue
            try:
                predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
                validate_result(predecessor)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exception:
                raise ValueError(f"Invalid predecessor result {prior['runId']}: {exception}") from exception
            if predecessor["recordKind"] != "campaign":
                raise ValueError(f"Predecessor is not a campaign result: {prior['runId']}")
            expected_predecessor = {
                "runId": prior["runId"], "model": prior["model"],
                "reasoningEffort": prior["reasoningEffort"],
                "machineId": prior["machineId"], "trial": prior["trial"],
                "orderPosition": prior["orderPosition"], "planHash": current_plan_hash,
            }
            if any(predecessor.get(field) != value for field, value in expected_predecessor.items()):
                raise ValueError(f"Predecessor result does not match plan: {prior['runId']}")
            if predecessor["status"] == "INFRA_FAILURE":
                unresolved_predecessors.append(prior["runId"])
        if missing_predecessors:
            raise ValueError(
                f"Run order violation; first incomplete predecessor is {missing_predecessors[0]}"
            )
        if unresolved_predecessors and not args.continue_after_unresolved:
            raise ValueError(f"Predecessor result is unresolved: {unresolved_predecessors[0]}")
        if args.backend == "docker":
            ensure_image(EVALUATOR_IMAGE, "docker/evaluator.Dockerfile")
        output = run_artifact_path(RUNS / "results", job["runId"], ".json")
        if output.exists() and not args.replace:
            raise FileExistsError(f"Campaign result already exists: {output}")
        if args.replace and not output.exists():
            raise FileNotFoundError(f"Replacement requires an existing campaign result: {output}")
        replacement_audit = None
        if output.exists() and args.replace:
            previous = json.loads(output.read_text(encoding="utf-8"))
            validate_result(previous)
            if (
                previous["recordKind"] != "campaign"
                or previous["runId"] != job["runId"]
                or previous["planHash"] != current_plan_hash
                or previous["status"] != "INFRA_FAILURE"
            ):
                raise ValueError(
                    f"Only an infrastructure-failure result from this campaign is replaceable: {output}"
                )
            if previous["failureKind"] in {
                "generation-launcher", "generation-inert", "generation-unavailable"
            }:
                raise ValueError(
                    "This failure produced no sampled artifact and cannot be "
                    "replaced without resampling; freeze the campaign and apply its preregistered "
                    "infrastructure-failure policy"
                )
            previous_hash = sha256_file(output)
            archive_root = RUNS / "replacements" / job["runId"]
            archive = ensure_strict_descendant(
                archive_root / f"{previous_hash}.json", RUNS / "replacements"
            )
            if archive.exists() and sha256_file(archive) != previous_hash:
                raise ValueError(f"Replacement archive hash collision: {archive}")
            if not archive.exists():
                atomic_write(archive, output.read_bytes(), mode=0o600)
            replacement_audit = {
                "previousResultHash": previous_hash,
                "previousStatus": previous["status"],
                "reason": args.replacement_reason,
                "archivedResult": archive.relative_to(ROOT).as_posix(),
            }
        if args.replace:
            candidate = ensure_strict_descendant(
                RUNS / "workspaces" / job["runId"], RUNS / "workspaces"
            )
            if not candidate.is_dir():
                raise FileNotFoundError(
                    f"Replacement requires the retained candidate workspace: {candidate}"
                )
        else:
            candidate = generate_candidate(job, auth_path=args.auth_file)
        metadata_path = run_artifact_path(RUNS / "generations", job["runId"], ".meta.json")
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Missing generation metadata for retained candidate: {metadata_path}")
        generation_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if generation_metadata.get("runId") != job["runId"]:
            raise ValueError(f"Generation metadata does not match run: {job['runId']}")
        if args.replace:
            retained_hash = candidate_hash(candidate)
            if retained_hash != previous["candidateHash"]:
                raise ValueError(
                    f"Retained candidate differs from archived result for {job['runId']}"
                )
            if (
                generation_metadata.get("generatorImage") != previous["generatorImage"]
                or generation_metadata.get("durationSeconds")
                != previous["generationDurationSeconds"]
                or generation_metadata.get("promptHash") != previous["promptHash"]
            ):
                raise ValueError(
                    f"Generation metadata differs from archived result for {job['runId']}"
                )
        campaign_metadata: dict[str, Any] = {
            "planHash": current_plan_hash,
            "trial": job["trial"],
            "orderPosition": job["orderPosition"],
            "generationDurationSeconds": generation_metadata["durationSeconds"],
            "generatorImage": generation_metadata["generatorImage"],
        }
        if unresolved_predecessors:
            campaign_metadata["queueAudit"] = {
                "continuedAfterUnresolvedRunIds": unresolved_predecessors,
            }
        if replacement_audit is not None:
            campaign_metadata["replacementAudit"] = replacement_audit
        generation_failure = None if args.replace else generation_failure_kind(
            generation_metadata, candidate,
            run_artifact_path(RUNS / "generations", job["runId"], ".jsonl"),
        )
        if generation_failure is not None:
            report = campaign_generation_failure_result(
                candidate, job, backend=args.backend, repeat=args.repeat,
                campaign_metadata=campaign_metadata,
                status=generation_failure[0], failure_kind=generation_failure[1],
            )
        else:
            report = evaluate_candidate(
                candidate, run_id=job["runId"], model=job["model"], effort=job["reasoningEffort"],
                machine=job["machineId"], repeat=args.repeat, timeout=args.timeout, backend=args.backend,
                isolation="container-strong",
                record_kind="campaign", campaign_metadata=campaign_metadata,
            )
        validate_result(report)
        save_json(output, report, private=True)
        print(json.dumps({"status": report["status"], "output": str(output)}, indent=2))
        return 0 if report["status"] == "PASS" else 1

    if args.command == "aggregate":
        payload = aggregate(args.paths, args.plan, allow_incomplete=args.allow_incomplete)
        save_json(args.output, payload)
        print(args.output)
        return 0

    if args.command == "publish":
        payload = publish_evidence_bundle(args.paths, args.plan)
        save_json(args.output, payload)
        print(args.output)
        return 0

    if args.command == "check-generator":
        report = check_generator(args.timeout)
        save_json(args.output, report)
        print(json.dumps({
            "passed": report["passed"],
            "checks": report["checks"],
            "observedCodexVersion": report["observedCodexVersion"],
            "namespaceSandbox": report["namespaceSandbox"],
            "output": str(args.output),
        }, indent=2))
        return 0 if report["passed"] else 1

    if args.command == "power":
        required = required_samples_per_configuration(
            args.baseline_rate,
            args.target_rate,
            comparisons=args.comparisons,
            familywise_alpha=args.familywise_alpha,
            power=args.power,
        )
        print(json.dumps({
            "requiredSamplesPerConfiguration": required,
            "comparisons": args.comparisons,
            "familywiseAlpha": args.familywise_alpha,
            "power": args.power,
            "baselineRate": args.baseline_rate,
            "targetRate": args.target_rate,
        }, indent=2, sort_keys=True))
        return 0

    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
