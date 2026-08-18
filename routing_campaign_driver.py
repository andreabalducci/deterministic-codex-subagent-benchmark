#!/usr/bin/env python3
"""Run one routing-campaign machine sequentially with audited infra retries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import harness
import routing_campaign
import routing_sequential


ROOT = Path(__file__).resolve().parent
DEFAULT_RUN_ROOT = ROOT / "runs/routing"


class DriverError(ValueError):
    """Raised when campaign execution cannot safely proceed."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: Any, *, replace: bool = True) -> None:
    harness.save_json(path, value, private=True, replace=replace)


def machine_jobs(plan: dict[str, Any], machine_id: str) -> list[dict[str, Any]]:
    jobs = [job for job in plan["jobs"] if job["machineId"] == machine_id]
    if not jobs:
        raise DriverError(f"plan has no jobs for machine {machine_id}")
    return jobs


def result_path(run_root: Path, run_id: str) -> Path:
    return run_root / "results" / f"{run_id}.json"


def status(
    plan: dict[str, Any], machine_id: str, run_root: Path,
    protocol: dict[str, Any] | None = None,
    authorized_run_ids: set[str] | None = None,
) -> dict[str, Any]:
    counts = {"pending": 0, "pass": 0, "candidateFailure": 0, "infraFailure": 0, "invalid": 0}
    next_run_id = None
    jobs = machine_jobs(plan, machine_id)
    if authorized_run_ids is not None:
        jobs = [job for job in jobs if job["runId"] in authorized_run_ids]
    for job in jobs:
        path = result_path(run_root, job["runId"])
        if not path.exists():
            counts["pending"] += 1
            if next_run_id is None:
                next_run_id = job["runId"]
            continue
        try:
            result = load_json(path)
            if protocol is not None:
                routing_campaign.validate_results([result], plan, protocol)
        except (OSError, json.JSONDecodeError):
            counts["invalid"] += 1
            continue
        except routing_campaign.ValidationError:
            counts["invalid"] += 1
            continue
        key = {
            "PASS": "pass", "CANDIDATE_FAILURE": "candidateFailure", "INFRA_FAILURE": "infraFailure"
        }.get(result.get("status"), "invalid")
        counts[key] += 1
        if key == "infraFailure" and next_run_id is None:
            next_run_id = job["runId"]
    return {"machineId": machine_id, "jobs": len(jobs), **counts, "nextRunId": next_run_id}


def runner_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable, str(ROOT / "routing_runner.py"),
        "--protocol", str(args.protocol), "--plan", str(args.plan),
        "--run-id", run_id, "--machine-id", args.machine_id,
        "--auth-file", str(args.auth_file), "--run-root", str(args.run_root),
        "--runtime-manifest", str(args.runtime_manifest), "--preflight", str(args.preflight),
        "--agent-timeout", str(args.agent_timeout),
    ]
    sequential_state = getattr(args, "sequential_state", None)
    sequential_manifest = getattr(args, "sequential_manifest", None)
    if sequential_state is not None:
        if sequential_manifest is None:
            raise DriverError("--sequential-state requires --sequential-manifest")
        command.extend(["--sequential-state", str(sequential_state), "--sequential-manifest", str(sequential_manifest)])
    return command


def _sequential_inputs(args: argparse.Namespace, plan: dict[str, Any], protocol: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    state_path = getattr(args, "sequential_state", None)
    if state_path is None:
        return None
    manifest_path = getattr(args, "sequential_manifest", None)
    if manifest_path is None:
        raise DriverError("--sequential-state requires --sequential-manifest")
    manifest = load_json(manifest_path)
    state = load_json(state_path)
    routing_sequential.validate_manifest(manifest, plan, protocol)
    routing_sequential.validate_state(state, manifest, plan, protocol)
    results = {}
    for path in (args.run_root / "results").glob("*.json"):
        try:
            result = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and isinstance(result.get("runId"), str):
            results[result["runId"]] = result
    routing_sequential.replay_state(state, manifest, plan, protocol, results)
    return manifest, state


def run_machine(
    args: argparse.Namespace, plan: dict[str, Any], protocol: dict[str, Any] | None = None
) -> int:
    sequential = _sequential_inputs(args, plan, protocol) if protocol is not None else None
    jobs = machine_jobs(plan, args.machine_id)
    if sequential is not None:
        _, state = sequential
        authorized = {
            run_id for item in state["authorized"] for run_id in item["runIds"]
            if next(job for job in plan["jobs"] if job["runId"] == run_id)["machineId"] == args.machine_id
        }
        jobs = [job for job in jobs if job["runId"] in authorized]
    for job in jobs:
        path = result_path(args.run_root, job["runId"])
        if path.exists():
            result = load_json(path)
            if protocol is not None:
                routing_campaign.validate_results([result], plan, protocol)
            if result.get("status") == "INFRA_FAILURE":
                raise DriverError(
                    f"campaign paused at {job['runId']}; use retry-infra after fixing infrastructure"
                )
            continue
        completed = subprocess.run(runner_command(args, job["runId"]), check=False)
        if completed.returncode == 2:
            raise DriverError(f"campaign paused after infrastructure failure {job['runId']}")
        if completed.returncode != 0:
            raise DriverError(f"runner exited {completed.returncode} for {job['runId']}")
    return 0


def _artifact_paths(run_root: Path, run_id: str) -> list[Path]:
    return [
        run_root / "results" / f"{run_id}.json",
        run_root / "transcripts" / f"{run_id}.jsonl",
        run_root / "transcripts" / f"{run_id}.meta.json",
        run_root / "workspaces" / run_id,
    ]


def _inventory(path: Path) -> list[dict[str, Any]]:
    if path.is_file() and not path.is_symlink():
        return [{"path": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}]
    entries = []
    if path.is_dir() and not path.is_symlink():
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = sorted(name for name in dirs if not (Path(root) / name).is_symlink())
            for name in sorted(files):
                item = Path(root) / name
                relative = item.relative_to(path).as_posix()
                if item.is_symlink():
                    entries.append({"path": relative, "symlink": os.readlink(item)})
                else:
                    entries.append({
                        "path": relative, "size": item.stat().st_size, "sha256": sha256_file(item)
                    })
    return entries


def archive_infra_attempt(
    plan: dict[str, Any], machine_id: str, run_root: Path, run_id: str,
    protocol: dict[str, Any] | None = None,
) -> Path:
    job = next((job for job in machine_jobs(plan, machine_id) if job["runId"] == run_id), None)
    if job is None:
        raise DriverError("retry run ID is not assigned to this machine")
    current_result = result_path(run_root, run_id)
    observed = load_json(current_result) if current_result.is_file() else None
    if protocol is not None and observed is not None:
        routing_campaign.validate_results([observed], plan, protocol)
    if observed is None or observed.get("status") != "INFRA_FAILURE":
        raise DriverError("retry-infra requires a current INFRA_FAILURE result")
    archive_root = run_root / "replacements" / run_id
    attempt = 1
    while (archive_root / f"attempt-{attempt}").exists():
        attempt += 1
    destination = archive_root / f"attempt-{attempt}"
    destination.mkdir(parents=True, exist_ok=False)
    moved, inventory = [], {}
    for source in _artifact_paths(run_root, run_id):
        if not source.exists() and not source.is_symlink():
            continue
        inventory[source.name] = _inventory(source)
        target = destination / source.name
        shutil.move(str(source), str(target))
        moved.append(source.name)
    if f"{run_id}.json" not in moved:
        raise DriverError("infra result disappeared during archival")
    manifest = {
        "schemaVersion": 1,
        "recordKind": "routing-infra-replacement-attempt",
        "runId": run_id,
        "machineId": machine_id,
        "attempt": attempt,
        "planHash": routing_campaign.value_hash(plan),
        "movedArtifacts": moved,
        "inventory": inventory,
    }
    save_json(destination / "archive-manifest.json", manifest)
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "run-machine", "retry-infra", "sequential-init", "sequential-advance", "sequential-analyze"))
    parser.add_argument("--protocol", type=Path, default=routing_campaign.DEFAULT_PROTOCOL)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--machine-id")
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--auth-file", type=Path)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--runtime-manifest", type=Path, default=ROOT / "protocols/routing-runtime-v1.json")
    parser.add_argument("--agent-timeout", type=int, default=1800)
    parser.add_argument("--run-id")
    parser.add_argument("--sequential-state", type=Path)
    parser.add_argument("--sequential-manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    protocol = routing_campaign.validate_protocol(load_json(args.protocol))
    plan = load_json(args.plan)
    routing_campaign.validate_plan(plan, protocol)
    args.run_root = args.run_root.resolve()
    if args.command == "sequential-init":
        if args.sequential_manifest is None or args.sequential_state is None:
            raise DriverError("sequential-init requires --sequential-manifest and --sequential-state")
        if args.sequential_manifest.exists() or args.sequential_state.exists():
            raise DriverError("sequential-init never overwrites an existing manifest or state")
        manifest = routing_sequential.make_manifest(plan, protocol)
        state = routing_sequential.make_initial_state(manifest, plan, protocol)
        save_json(args.sequential_manifest, manifest, replace=False)
        save_json(args.sequential_state, state, replace=False)
        print(json.dumps({"manifestHash": manifest["manifestHash"], "stateHash": state["stateHash"]}, sort_keys=True))
        return 0
    if args.command in {"sequential-advance", "sequential-analyze"}:
        if args.sequential_manifest is None or args.sequential_state is None:
            raise DriverError(f"{args.command} requires --sequential-manifest and --sequential-state")
        manifest, state = _sequential_inputs(args, plan, protocol)  # type: ignore[assignment]
        results = {}
        for path in (args.run_root / "results").glob("*.json"):
            try:
                result = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(result, dict) and "runId" in result:
                results[result["runId"]] = result
        if args.command == "sequential-analyze":
            print(json.dumps(
                routing_sequential.analyze_state(
                    state, manifest, plan, protocol, results
                ), indent=2, sort_keys=True,
            ))
            return 0
        next_state = routing_sequential.advance_state(state, manifest, plan, protocol, results)
        save_json(args.sequential_state, next_state)
        print(json.dumps({"stateHash": next_state["stateHash"], "complete": next_state["complete"]}, sort_keys=True))
        return 0
    if not args.machine_id:
        raise DriverError(f"{args.command} requires --machine-id")
    if args.command == "status":
        sequential = _sequential_inputs(args, plan, protocol)
        authorized = None if sequential is None else {
            run_id
            for item in sequential[1]["authorized"]
            for run_id in item["runIds"]
        }
        print(json.dumps(
            status(plan, args.machine_id, args.run_root, protocol, authorized),
            indent=2, sort_keys=True,
        ))
        return 0
    if args.auth_file is None or args.preflight is None:
        raise DriverError("run-machine and retry-infra require --auth-file and --preflight")
    if args.command == "retry-infra":
        if not args.run_id:
            raise DriverError("retry-infra requires --run-id")
        archived = archive_infra_attempt(
            plan, args.machine_id, args.run_root, args.run_id, protocol
        )
        print(json.dumps({"archived": str(archived)}, sort_keys=True))
        completed = subprocess.run(runner_command(args, args.run_id), check=False)
        return completed.returncode
    return run_machine(args, plan, protocol)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DriverError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
