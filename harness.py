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
FIXTURE = ROOT / "fixtures" / "async-cache-v1"
STARTER = FIXTURE / "starter"
HIDDEN = FIXTURE / "hidden" / "Cache.HiddenTests"
REFERENCE = FIXTURE / "reference" / "AsyncExpiringCache.cs"
MANIFEST = FIXTURE / "fixture-manifest.json"
MATRIX = ROOT / "matrix.json"
RUNS = ROOT / "runs"
EVALUATOR_IMAGE = "codex-bench-evaluator:10.0.301"
GENERATOR_IMAGE = "codex-bench-generator:0.147.0"
IGNORED_PARTS = {"bin", "obj", ".git", "__pycache__"}
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
MAX_EVALUATOR_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_GENERATOR_OUTPUT_BYTES = 16 * 1024 * 1024
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
}
PUBLIC_PASS_MARKER = "CODEX_BENCH_PUBLIC_PASS_V1"
HIDDEN_PASS_MARKER = "CODEX_BENCH_HIDDEN_PASS_V1"


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
        "fixtureId": "async-cache-v1",
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


def ensure_under(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    root = parent.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Refusing path outside {root}: {resolved}")


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(f"Invalid opaque run ID: {run_id!r}")
    return run_id


def load_or_create_id_key(path: Path) -> bytes:
    path = path.resolve()
    ensure_under(path, RUNS)
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


def materialize(destination: Path, *, replace: bool = False) -> None:
    destination = destination.resolve()
    if destination.exists():
        if not replace:
            raise FileExistsError(f"Destination already exists: {destination}")
        ensure_under(destination, RUNS / "workspaces")
        shutil.rmtree(destination)
    shutil.copytree(STARTER, destination)
    shutil.copy2(FIXTURE / "TASK.md", destination / "TASK.md")


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
        text = path.read_text(encoding="utf-8")
        for name, pattern in POLICY_PATTERNS.items():
            if pattern.search(text):
                violations.append(f"{name}: {path.relative_to(candidate).as_posix()}")
    return violations


def repository_provenance() -> dict[str, Any]:
    tracked = [
        ROOT / "harness.py",
        ROOT / "matrix.json",
        ROOT / "schemas" / "run-result.schema.json",
        ROOT / "global.json",
        ROOT / "Directory.Build.props",
        ROOT / "docker" / "evaluator.Dockerfile",
        ROOT / "docker" / "generator.Dockerfile",
        ROOT / "docker" / "package.json",
        ROOT / "docker" / "package-lock.json",
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
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
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


def decoded_timeout_stream(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def read_capped(stream: Any, limit: int) -> tuple[str, bool]:
    stream.seek(0)
    value = stream.read(limit + 1)
    exceeded = len(value) > limit
    return value[:limit].decode("utf-8", errors="replace"), exceeded


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
    exceeded = [False, False]
    stopped = threading.Event()

    def stop_for_limit() -> None:
        if stopped.is_set():
            return
        stopped.set()
        if timeout_callback is not None:
            timeout_callback()
        process.kill()

    def drain(stream: Any, index: int) -> None:
        while True:
            block = stream.read(65536)
            if not block:
                return
            remaining = limit - len(buffers[index])
            if remaining > 0:
                buffers[index].extend(block[:remaining])
            if len(block) > remaining:
                exceeded[index] = True
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
        "outputLimitExceeded": any(exceeded),
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
) -> dict[str, Any]:
    if repeat <= 0 or timeout <= 0:
        raise ValueError("repeat and timeout must be positive integers")
    validate_run_id(run_id) if not run_id.startswith("verify-") else None
    if backend == "native" and not trusted:
        raise ValueError("Native evaluation is restricted to trusted fixture verification")
    started = time.monotonic()
    integrity = candidate_integrity(candidate)
    policy = policy_violations(candidate) if not integrity else []
    manifest_ok, manifest_errors = verify_manifest()
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "runId": run_id,
        "fixtureId": "async-cache-v1",
        "fixtureManifestHash": sha256_file(MANIFEST) if MANIFEST.exists() else None,
        "candidateHash": candidate_hash(candidate) if not integrity else None,
        "promptHash": sha256_bytes(AGENT_PROMPT.encode("utf-8")),
        "model": model,
        "reasoningEffort": effort,
        "fastMode": False,
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
    }

    if not manifest_ok:
        result["status"] = "INFRA_FAILURE"
        result["durationSeconds"] = round(time.monotonic() - started, 6)
        return result
    if integrity or policy:
        result["status"] = "CANDIDATE_FAILURE"
        result["durationSeconds"] = round(time.monotonic() - started, 6)
        return result

    with tempfile.TemporaryDirectory(prefix="codex-bench-eval-") as temp_name:
        root = Path(temp_name)
        workspace = root / "candidate-build"
        shutil.copytree(candidate, workspace, ignore=shutil.ignore_patterns("bin", "obj", ".git"))
        shutil.copy2(ROOT / "global.json", workspace / "global.json")
        shutil.copy2(ROOT / "Directory.Build.props", workspace / "Directory.Build.props")
        status = "PASS"
        public_runtime = workspace / "public-published"
        public_build_command = [
            "dotnet", "publish", "Cache.PublicTests/Cache.PublicTests.csproj",
            "--configuration", "Release", "--output", "public-published",
        ]
        execution = run_with_timeout_confirmation(public_build_command, workspace, timeout, backend)
        result["tests"].append({"name": "public-build", **execution})
        attempt = last_attempt(execution)
        if attempt["launcherFailure"] or execution["hadTimeout"]:
            status = "INDETERMINATE_TIMEOUT" if execution["confirmedTimeout"] else "INFRA_FAILURE"
        elif attempt["outputLimitExceeded"]:
            status = "CANDIDATE_FAILURE"
        elif attempt["exitCode"] != 0:
            status = "CANDIDATE_FAILURE"

        if status == "PASS":
            public_command = ["dotnet", "Cache.PublicTests.dll"]
            execution = run_with_timeout_confirmation(
                public_command, public_runtime, timeout, backend, readonly=backend == "docker"
            )
            result["tests"].append({"name": "public", **execution})
            attempt = last_attempt(execution)
            if attempt["launcherFailure"] or execution["hadTimeout"]:
                status = "INDETERMINATE_TIMEOUT" if execution["confirmedTimeout"] else "INFRA_FAILURE"
            elif attempt["outputLimitExceeded"] or attempt["exitCode"] != 0:
                status = "CANDIDATE_FAILURE"
            elif attempt["stdout"].count(PUBLIC_PASS_MARKER) != 1:
                status = "CANDIDATE_FAILURE"

        runtime = root / "hidden-build" / "published"
        if status == "PASS":
            hidden_build = root / "hidden-build"
            shutil.copytree(HIDDEN, hidden_build / "Cache.HiddenTests")
            candidate_binary = hidden_build / "Candidate"
            candidate_binary.mkdir(parents=True)
            built_core = workspace / "Cache.Core" / "bin" / "Release" / "net10.0"
            for path in built_core.glob("Cache.Core.*"):
                if path.is_file():
                    shutil.copy2(path, candidate_binary / path.name)
            shutil.copy2(ROOT / "global.json", hidden_build / "global.json")
            shutil.copy2(ROOT / "Directory.Build.props", hidden_build / "Directory.Build.props")
            publish_command = [
                "dotnet", "publish", "Cache.HiddenTests/Cache.HiddenTests.csproj",
                "--configuration", "Release",
                "--output", "published",
                "-p:CandidateAssemblyPath=../Candidate/Cache.Core.dll",
            ]
            execution = run_with_timeout_confirmation(publish_command, hidden_build, timeout, backend)
            result["tests"].append({"name": "hidden-build", **execution})
            attempt = last_attempt(execution)
            if attempt["launcherFailure"] or execution["hadTimeout"]:
                status = "INDETERMINATE_TIMEOUT" if execution["confirmedTimeout"] else "INFRA_FAILURE"
            elif attempt["outputLimitExceeded"]:
                status = "CANDIDATE_FAILURE"
            elif attempt["exitCode"] != 0:
                status = "CANDIDATE_FAILURE"

        if status == "PASS":
            hidden_command = ["dotnet", "Cache.HiddenTests.dll"]
            for index in range(1, repeat + 1):
                execution = run_with_timeout_confirmation(
                    hidden_command, runtime, timeout, backend, readonly=backend == "docker"
                )
                result["tests"].append({"name": f"hidden-{index}", **execution})
                attempt = last_attempt(execution)
                if attempt["launcherFailure"] or execution["hadTimeout"]:
                    status = "INDETERMINATE_TIMEOUT" if execution["confirmedTimeout"] else "INFRA_FAILURE"
                    break
                if attempt["outputLimitExceeded"]:
                    status = "CANDIDATE_FAILURE"
                    break
                if attempt["exitCode"] != 0:
                    status = "CANDIDATE_FAILURE"
                    break
                if attempt["stdout"].count(HIDDEN_PASS_MARKER) != 1:
                    status = "CANDIDATE_FAILURE"
                    break
        result["status"] = status

    result["durationSeconds"] = round(time.monotonic() - started, 6)
    return result


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def image_spec_hash(dockerfile: str) -> str:
    paths = [ROOT / dockerfile]
    if "generator" in dockerfile:
        paths.extend([ROOT / "docker" / "package.json", ROOT / "docker" / "package-lock.json"])
    payload = {path.relative_to(ROOT).as_posix(): sha256_file(path) for path in paths}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


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
    path = configured.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Benchmark auth file not found: {path}")
    if not path.is_file() or path.is_symlink():
        raise ValueError("Benchmark auth path must be a regular, non-symlink file")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise PermissionError("Benchmark auth file must not be accessible by group or other users")
    return path


def generate_candidate(
    job: dict[str, Any], *, auth_path: Path | None, replace: bool = False
) -> Path:
    run_id = validate_run_id(job["runId"])
    candidate = RUNS / "workspaces" / run_id
    ensure_under(candidate, RUNS / "workspaces")
    materialize(candidate, replace=replace)
    ensure_image(GENERATOR_IMAGE, "docker/generator.Dockerfile")
    log_dir = RUNS / "generations"
    log_dir.mkdir(parents=True, exist_ok=True)
    container_name = f"codex-bench-gen-{run_id}"
    command = [
        "docker", "run", "--rm", "--interactive", "--name", container_name,
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "512", "--memory", "2g", "--cpus", "4",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
        "--tmpfs", "/codex-home:rw,noexec,nosuid,size=32m",
        "--env", "CODEX_HOME=/codex-home",
        "--mount", f"type=bind,src={benchmark_auth_file(auth_path)},dst=/codex-home/auth.json,readonly",
        "--mount", f"type=bind,src={candidate.resolve()},dst=/workspace",
        "--workdir", "/workspace",
        GENERATOR_IMAGE,
        "codex", "exec",
        "--sandbox", "workspace-write",
        "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
        "--json", "--model", job["model"],
        "--config", f'model_reasoning_effort="{job["reasoningEffort"]}"',
        "-",
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
    if completed["timedOut"]:
        raise RuntimeError(f"Agent timed out for run {run_id}")

    log_path = log_dir / f"{run_id}.jsonl"
    log_path.write_text(completed["stdout"], encoding="utf-8")
    log_path.chmod(0o600)
    metadata = {
        "runId": run_id,
        "exitCode": completed["exitCode"],
        "durationSeconds": completed["durationSeconds"],
        "stderr": completed["stderr"],
        "outputLimitExceeded": completed["outputLimitExceeded"],
        "promptHash": sha256_bytes(AGENT_PROMPT.encode("utf-8")),
        "generatorImage": docker_image_info(GENERATOR_IMAGE),
        "isolation": "container-strong",
    }
    metadata_path = log_dir / f"{run_id}.meta.json"
    save_json(metadata_path, metadata)
    metadata_path.chmod(0o600)
    if completed["exitCode"] != 0 or completed["outputLimitExceeded"]:
        raise RuntimeError(f"Agent failed for run {run_id}; see generation metadata")
    return candidate


def load_matrix() -> list[dict[str, str]]:
    return json.loads(MATRIX.read_text(encoding="utf-8"))["configurations"]


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


def make_plan(trials: int, seed: str, machines: list[str], id_key: bytes) -> dict[str, Any]:
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
                    message = f"{seed}:async-cache-v1:{trial}:{config['id']}".encode("utf-8")
                    opaque = hmac.new(id_key, message, hashlib.sha256).hexdigest()[:16]
                    jobs.append({
                        "runId": opaque,
                        "trial": trial,
                        "orderPosition": position,
                        "machineId": machine,
                        **config,
                    })
                trial += 1
    return {
        "schemaVersion": 1,
        "fixtureId": "async-cache-v1",
        "seed": seed,
        "trials": trials,
        "machines": machines,
        "jobs": jobs,
    }


def wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return centre - margin, centre + margin


def validate_result(result: dict[str, Any]) -> None:
    required = {
        "schemaVersion", "runId", "fixtureId", "fixtureManifestHash", "candidateHash",
        "promptHash", "model", "reasoningEffort", "machineId", "status", "tests",
        "planHash", "trial", "orderPosition", "provenance", "generationDurationSeconds",
        "repeatCount", "backend", "evaluatorImage", "generatorImage", "durationSeconds",
    }
    missing = sorted(required - set(result))
    if missing:
        raise ValueError(f"Result is missing required fields: {missing}")
    validate_run_id(result["runId"])
    if result["schemaVersion"] != 1 or result["fixtureId"] != "async-cache-v1":
        raise ValueError(f"Unsupported result identity for {result['runId']}")
    if result["status"] not in {
        "PASS", "CANDIDATE_FAILURE", "INFRA_FAILURE", "INDETERMINATE_TIMEOUT"
    }:
        raise ValueError(f"Invalid status for {result['runId']}: {result['status']}")
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
        if not isinstance(test["attempts"], list) or not 1 <= len(test["attempts"]) <= 2:
            raise ValueError(f"Invalid attempts for {result['runId']}")
        for attempt in test["attempts"]:
            required_attempt = {
                "command", "exitCode", "stdout", "stderr", "timedOut",
                "launcherFailure", "outputLimitExceeded", "durationSeconds",
            }
            if not isinstance(attempt, dict) or not required_attempt.issubset(attempt):
                raise ValueError(f"Invalid attempt evidence for {result['runId']}")
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


def quartiles(values: list[float]) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        return [values[0], values[0]]
    cuts = statistics.quantiles(values, n=4, method="inclusive")
    return [cuts[0], cuts[2]]


def aggregate(
    paths: list[Path], plan_path: Path, *, allow_incomplete: bool = False
) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_hash = sha256_file(plan_path)
    planned = {job["runId"]: job for job in plan["jobs"]}
    groups: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    all_results: list[dict[str, Any]] = []
    for path in paths:
        result = json.loads(path.read_text(encoding="utf-8"))
        validate_result(result)
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
        if result["fixtureManifestHash"] != sha256_file(MANIFEST):
            raise ValueError(f"Fixture mismatch for {run_id}")
        if result["promptHash"] != sha256_bytes(AGENT_PROMPT.encode("utf-8")):
            raise ValueError(f"Prompt mismatch for {run_id}")
        key = f"{result['model']}:{result['reasoningEffort']}"
        groups.setdefault(key, []).append(result)
        all_results.append(result)

    missing_runs = sorted(set(planned) - seen)
    unresolved = sorted(
        result["runId"]
        for result in all_results
        if result["status"] in {"INFRA_FAILURE", "INDETERMINATE_TIMEOUT"}
    )
    if not allow_incomplete and (missing_runs or unresolved):
        raise ValueError(
            f"Official aggregation requires a complete resolved cohort; "
            f"missing={len(missing_runs)}, unresolved={len(unresolved)}"
        )

    candidate_hash_counts: dict[str, int] = {}
    for result in all_results:
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
    for key, results in sorted(groups.items()):
        eligible = [
            result for result in results
            if result["status"] not in {"INFRA_FAILURE", "INDETERMINATE_TIMEOUT"}
        ]
        successes = sum(result["status"] == "PASS" for result in eligible)
        low, high = wilson(successes, len(eligible))
        durations = [result["durationSeconds"] for result in eligible]
        generation_durations = [result["generationDurationSeconds"] for result in eligible]
        summary["groups"][key] = {
            "totalRuns": len(results),
            "eligibleRuns": len(eligible),
            "passes": successes,
            "passRate": successes / len(eligible) if eligible else None,
            "wilson95": [low, high] if eligible else None,
            "medianEvaluationSeconds": statistics.median(durations) if durations else None,
            "evaluationIqrSeconds": quartiles(durations),
            "medianGenerationSeconds": statistics.median(generation_durations) if generation_durations else None,
            "generationIqrSeconds": quartiles(generation_durations),
            "byMachine": {
                machine: {
                    "runs": sum(result["machineId"] == machine for result in eligible),
                    "passes": sum(
                        result["machineId"] == machine and result["status"] == "PASS"
                        for result in eligible
                    ),
                }
                for machine in sorted({result["machineId"] for result in eligible})
            },
            "statuses": {
                status: sum(result["status"] == status for result in results)
                for status in sorted({result["status"] for result in results})
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
            shutil.copytree(STARTER, candidate)
            shutil.copy2(FIXTURE / "TASK.md", candidate / "TASK.md")
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
    verify.add_argument("--timeout", type=positive_int, default=30)
    verify.add_argument("--backend", choices=["native", "docker"], default="native")
    verify.add_argument("--output", type=Path, default=RUNS / "verification.json")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--candidate", type=Path, required=True)
    evaluate.add_argument("--run-id", required=True)
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--effort", required=True)
    evaluate.add_argument("--machine", default=platform.node() or "local")
    evaluate.add_argument("--repeat", type=positive_int, default=20)
    evaluate.add_argument("--timeout", type=positive_int, default=30)
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
    job.add_argument("--timeout", type=positive_int, default=30)
    job.add_argument("--backend", choices=["docker"], default="docker")
    job.add_argument("--replace", action="store_true")
    job.add_argument("--auth-file", type=Path)
    job.add_argument("--machine-id", default=platform.node() or "local")

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("paths", nargs="+", type=Path)
    aggregate_parser.add_argument("--plan", type=Path, default=RUNS / "plan.json")
    aggregate_parser.add_argument("--allow-incomplete", action="store_true")
    aggregate_parser.add_argument("--output", type=Path, default=RUNS / "summary.json")
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
        save_json(args.output, report)
        print(json.dumps({"passed": report["passed"], "output": str(args.output)}, indent=2))
        return 0 if report["passed"] else 1

    if args.command == "evaluate":
        if args.backend == "docker":
            ensure_image(EVALUATOR_IMAGE, "docker/evaluator.Dockerfile")
        report = evaluate_candidate(
            Path(os.path.abspath(args.candidate)), run_id=args.run_id, model=args.model, effort=args.effort,
            machine=args.machine, repeat=args.repeat, timeout=args.timeout, backend=args.backend,
            isolation=args.isolation,
        )
        output = args.output or RUNS / "results" / f"{args.run_id}.json"
        save_json(output, report)
        print(json.dumps({"status": report["status"], "output": str(output)}, indent=2))
        return 0 if report["status"] == "PASS" else 1

    if args.command == "plan":
        machines = [machine.strip() for machine in args.machines.split(",") if machine.strip()]
        if not machines:
            raise ValueError("At least one machine is required")
        payload = make_plan(args.trials, args.seed, machines, load_or_create_id_key(args.id_key_file))
        save_json(args.output, payload)
        args.output.chmod(0o600)
        blinded = {
            "schemaVersion": payload["schemaVersion"],
            "fixtureId": payload["fixtureId"],
            "jobs": [
                {key: job[key] for key in ["runId", "trial", "orderPosition", "machineId"]}
                for job in payload["jobs"]
            ],
        }
        save_json(args.blinded_output, blinded)
        print(json.dumps({
            "jobs": len(payload["jobs"]),
            "output": str(args.output),
            "planHash": sha256_file(args.output),
            "blindedOutput": str(args.blinded_output),
            "blindedPlanHash": sha256_file(args.blinded_output),
        }, indent=2))
        return 0

    if args.command == "run-job":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
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
        current_plan_hash = sha256_file(args.plan)
        for prior in plan["jobs"][:job_index]:
            if prior["machineId"] != args.machine_id:
                continue
            predecessor_path = RUNS / "results" / f"{prior['runId']}.json"
            if not predecessor_path.exists():
                missing_predecessors.append(prior["runId"])
                continue
            try:
                predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
                validate_result(predecessor)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exception:
                raise ValueError(f"Invalid predecessor result {prior['runId']}: {exception}") from exception
            expected_predecessor = {
                "runId": prior["runId"], "model": prior["model"],
                "reasoningEffort": prior["reasoningEffort"],
                "machineId": prior["machineId"], "trial": prior["trial"],
                "orderPosition": prior["orderPosition"], "planHash": current_plan_hash,
            }
            if any(predecessor.get(field) != value for field, value in expected_predecessor.items()):
                raise ValueError(f"Predecessor result does not match plan: {prior['runId']}")
            if predecessor["status"] in {"INFRA_FAILURE", "INDETERMINATE_TIMEOUT"}:
                raise ValueError(f"Predecessor result is unresolved: {prior['runId']}")
        if missing_predecessors:
            raise ValueError(
                f"Run order violation; first incomplete predecessor is {missing_predecessors[0]}"
            )
        candidate = generate_candidate(job, auth_path=args.auth_file, replace=args.replace)
        if args.backend == "docker":
            ensure_image(EVALUATOR_IMAGE, "docker/evaluator.Dockerfile")
        report = evaluate_candidate(
            candidate, run_id=job["runId"], model=job["model"], effort=job["reasoningEffort"],
            machine=job["machineId"], repeat=args.repeat, timeout=args.timeout, backend=args.backend,
            isolation="container-strong",
        )
        report.update({
            "planHash": sha256_file(args.plan),
            "trial": job["trial"],
            "orderPosition": job["orderPosition"],
            "generationDurationSeconds": json.loads(
                (RUNS / "generations" / f"{job['runId']}.meta.json").read_text(encoding="utf-8")
            )["durationSeconds"],
            "generatorImage": json.loads(
                (RUNS / "generations" / f"{job['runId']}.meta.json").read_text(encoding="utf-8")
            )["generatorImage"],
        })
        output = RUNS / "results" / f"{job['runId']}.json"
        save_json(output, report)
        output.chmod(0o600)
        print(json.dumps({"status": report["status"], "output": str(output)}, indent=2))
        return 0 if report["status"] == "PASS" else 1

    if args.command == "aggregate":
        payload = aggregate(args.paths, args.plan, allow_incomplete=args.allow_incomplete)
        save_json(args.output, payload)
        print(args.output)
        return 0

    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
