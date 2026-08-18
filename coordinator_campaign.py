#!/usr/bin/env python3
"""Freeze and deterministically schedule the live coordinator experiment.

This module does not execute Codex.  It makes the experimental unit explicit:
only the coordinator treatment varies; the worker policy hash is identical in
every scheduled job.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import routing_tasks

ROOT = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = ROOT / "protocols" / "coordinator-v1.json"
PROTOCOL_SCHEMA = ROOT / "schemas" / "coordinator-protocol.schema.json"
PLAN_SCHEMA = ROOT / "schemas" / "coordinator-plan.schema.json"


class ValidationError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _exact(value: Any, keys: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = set(value) if isinstance(value, dict) else set()
        raise ValidationError(f"{location} fields differ: {sorted(actual ^ keys)}")
    return value


PROTOCOL_KEYS = {
    "schemaVersion", "recordKind", "protocolId", "seed", "replicatesPerFixture",
    "machines", "fixtureIds", "coordinatorTreatments", "workerPolicy",
    "executionEnvelope", "acceptance", "sourceBinding", "bootstrapSeed",
    "bootstrapSamples", "familywiseAlpha", "analysis",
}
TREATMENT_KEYS = {"id", "model", "reasoningEffort"}
WORKER_KEYS = {
    "treatmentId", "model", "reasoningEffort", "workerCount", "concurrency",
    "maxSpawnDepth", "freshContext", "prompts",
}
ENVELOPE_KEYS = {
    "codexVersion", "serviceTier", "fastMode", "multiAgent", "ephemeral", "ignoreUserConfig",
    "ignoreRules", "network",
}
ACCEPTANCE_KEYS = {
    "requireExactWorkerPrompts", "requireAllWorkersCompleted",
    "requireNoNestedDelegation", "requireIntegrationPass",
    "maximumUnresolvedConflicts",
}
ANALYSIS_KEYS = {"candidateId", "absoluteQualityFloor", "nonInferiorityMargin",
                 "decisionComparatorId", "minimumDecisionGain"}


def validate_protocol(value: Any, *, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    protocol = _exact(value, PROTOCOL_KEYS, "$protocol")
    if protocol["schemaVersion"] != 1 or protocol["recordKind"] != "coordinator-protocol":
        raise ValidationError("unsupported coordinator protocol identity")
    if not all(isinstance(protocol[key], str) and protocol[key]
               for key in ("protocolId", "seed", "bootstrapSeed")):
        raise ValidationError("protocol identity and seed must be non-empty")
    if not isinstance(protocol["bootstrapSamples"], int) \
            or isinstance(protocol["bootstrapSamples"], bool) \
            or protocol["bootstrapSamples"] < 1000 \
            or not isinstance(protocol["familywiseAlpha"], (int, float)) \
            or isinstance(protocol["familywiseAlpha"], bool) \
            or not 0 < protocol["familywiseAlpha"] < 0.5:
        raise ValidationError("invalid coordinator bootstrap controls")
    if not isinstance(protocol["replicatesPerFixture"], int) \
            or isinstance(protocol["replicatesPerFixture"], bool) \
            or protocol["replicatesPerFixture"] <= 0:
        raise ValidationError("replicatesPerFixture must be positive")
    machines = protocol["machines"]
    fixtures = protocol["fixtureIds"]
    treatments = protocol["coordinatorTreatments"]
    if not isinstance(machines, list) or len(machines) < 3 or len(set(machines)) != len(machines):
        raise ValidationError("at least three unique machines are required")
    if protocol["replicatesPerFixture"] % len(machines):
        raise ValidationError("replicates must divide evenly across machines")
    if not isinstance(fixtures, list) or len(fixtures) != 12 or len(set(fixtures)) != 12:
        raise ValidationError("exactly twelve unique coordination fixtures are required")
    if not isinstance(treatments, list) or len(treatments) < 2 or len(treatments) % 2:
        raise ValidationError("an even treatment matrix is required for Williams balance")
    ids = []
    for index, treatment in enumerate(treatments):
        _exact(treatment, TREATMENT_KEYS, f"$protocol.coordinatorTreatments[{index}]")
        if treatment["reasoningEffort"] not in {"low", "medium", "high"}:
            raise ValidationError("unsupported coordinator reasoning effort")
        if not all(isinstance(treatment[k], str) and treatment[k] for k in TREATMENT_KEYS):
            raise ValidationError("invalid coordinator treatment")
        ids.append(treatment["id"])
    if len(ids) != len(set(ids)):
        raise ValidationError("coordinator treatment IDs must be unique")
    worker = _exact(protocol["workerPolicy"], WORKER_KEYS, "$protocol.workerPolicy")
    if worker["workerCount"] != 3 or worker["concurrency"] != 3 \
            or worker["maxSpawnDepth"] != 1 or worker["freshContext"] is not True:
        raise ValidationError("worker topology is not the frozen three-leaf policy")
    if worker["reasoningEffort"] not in {"low", "medium", "high"} \
            or not isinstance(worker["prompts"], list) or len(worker["prompts"]) != 3 \
            or len(set(worker["prompts"])) != 3 \
            or not all(isinstance(prompt, str) and len(prompt) >= 100 for prompt in worker["prompts"]):
        raise ValidationError("worker prompts or treatment are invalid")
    envelope = _exact(protocol["executionEnvelope"], ENVELOPE_KEYS, "$protocol.executionEnvelope")
    if not isinstance(envelope["codexVersion"], str) or not envelope["codexVersion"] \
            or envelope["multiAgent"] is not True or envelope["network"] is not True \
            or any(envelope[field] is not True for field in ("ephemeral", "ignoreUserConfig", "ignoreRules")) \
            or envelope["fastMode"] is not (envelope["serviceTier"] == "priority"):
        raise ValidationError("execution envelope violates coordinator experiment controls")
    acceptance = _exact(protocol["acceptance"], ACCEPTANCE_KEYS, "$protocol.acceptance")
    if any(acceptance[field] is not True for field in (
        "requireExactWorkerPrompts", "requireAllWorkersCompleted",
        "requireNoNestedDelegation", "requireIntegrationPass",
    )) or acceptance["maximumUnresolvedConflicts"] != 0:
        raise ValidationError("acceptance gates have drifted")
    analysis = _exact(protocol["analysis"], ANALYSIS_KEYS, "$protocol.analysis")
    if analysis["candidateId"] not in ids or analysis["decisionComparatorId"] not in ids \
            or analysis["candidateId"] == analysis["decisionComparatorId"] \
            or not 0 < analysis["absoluteQualityFloor"] <= 1 \
            or not 0 <= analysis["nonInferiorityMargin"] <= 0.5 \
            or not 0 <= analysis["minimumDecisionGain"] <= 1:
        raise ValidationError("invalid preregistered coordinator analysis")
    if catalog is not None:
        binding = _exact(protocol["sourceBinding"], {"catalogHash", "fixtureManifestHashes"},
                         "$protocol.sourceBinding")
        if binding["catalogHash"] != value_hash(catalog):
            raise ValidationError("coordinator catalog hash has drifted")
        by_id = {task["id"]: task for task in catalog["tasks"]}
        expected_manifests = {}
        for fixture_id in fixtures:
            task = by_id.get(fixture_id)
            if task is None or task.get("family") != "coordination-integration" \
                    or task.get("kind") != "confirmatory" or task.get("development") is not False:
                raise ValidationError(f"fixture {fixture_id} is not a confirmatory coordination task")
            expected_manifests[fixture_id] = task.get("manifestHash")
        if binding["fixtureManifestHashes"] != expected_manifests:
            raise ValidationError("coordinator fixture manifests have drifted")
    return protocol


def _seeded(values: list[Any], seed: str) -> list[Any]:
    return sorted(values, key=lambda item: hashlib.sha256(
        f"{seed}:{canonical_json(item)}".encode("utf-8")
    ).digest())


def _williams(treatments: list[dict[str, str]], seed: str) -> list[list[dict[str, str]]]:
    permuted = _seeded(treatments, f"{seed}:treatments")
    count = len(permuted)
    base = [0]
    for offset in range(1, count // 2 + 1):
        base.append(offset)
        if count - offset != offset:
            base.append(count - offset)
    return [[permuted[(index + row) % count] for index in base[:count]] for row in range(count)]


def make_plan(protocol: dict[str, Any], id_key: bytes) -> dict[str, Any]:
    validate_protocol(protocol)
    if len(id_key) < 16:
        raise ValidationError("HMAC key must contain at least 16 bytes")
    rows = _williams(protocol["coordinatorTreatments"], protocol["seed"])
    blocks_by_machine = {machine: [] for machine in protocol["machines"]}
    for fixture_id in _seeded(protocol["fixtureIds"], f"{protocol['seed']}:fixtures"):
        machines = _seeded(protocol["machines"], f"{protocol['seed']}:{fixture_id}:machines")
        for replicate in range(1, protocol["replicatesPerFixture"] + 1):
            blocks_by_machine[machines[(replicate - 1) % len(machines)]].append((fixture_id, replicate))
    jobs = []
    worker_hash = value_hash(protocol["workerPolicy"])
    protocol_hash = value_hash(protocol)
    for machine in protocol["machines"]:
        blocks = _seeded(blocks_by_machine[machine], f"{protocol['seed']}:{machine}:blocks")
        if len(blocks) % len(rows):
            raise ValidationError("machine block count cannot provide exact Williams balance")
        row_order = _seeded(list(range(len(rows))), f"{protocol['seed']}:{machine}:rows")
        order_position = 0
        for block_index, (fixture_id, replicate) in enumerate(blocks):
            for treatment in rows[row_order[block_index % len(rows)]]:
                order_position += 1
                message = f"{protocol_hash}:{fixture_id}:{replicate}:{treatment['id']}".encode()
                run_id = "coord-" + hmac.new(id_key, message, hashlib.sha256).hexdigest()[:24]
                jobs.append({
                    "runId": run_id, "fixtureId": fixture_id, "replicate": replicate,
                    "machineId": machine, "orderPosition": order_position,
                    "coordinatorTreatmentId": treatment["id"],
                    "coordinatorModel": treatment["model"],
                    "coordinatorReasoningEffort": treatment["reasoningEffort"],
                    "workerPolicyHash": worker_hash,
                })
    plan = {
        "schemaVersion": 1, "recordKind": "coordinator-plan",
        "protocolHash": protocol_hash, "workerPolicyHash": worker_hash, "jobs": jobs,
    }
    return validate_plan(plan, protocol)


PLAN_KEYS = {"schemaVersion", "recordKind", "protocolHash", "workerPolicyHash", "jobs"}
JOB_KEYS = {
    "runId", "fixtureId", "replicate", "machineId", "orderPosition",
    "coordinatorTreatmentId", "coordinatorModel", "coordinatorReasoningEffort",
    "workerPolicyHash",
}


def validate_plan(value: Any, protocol: dict[str, Any]) -> dict[str, Any]:
    validate_protocol(protocol)
    plan = _exact(value, PLAN_KEYS, "$plan")
    if plan["schemaVersion"] != 1 or plan["recordKind"] != "coordinator-plan" \
            or plan["protocolHash"] != value_hash(protocol) \
            or plan["workerPolicyHash"] != value_hash(protocol["workerPolicy"]):
        raise ValidationError("plan does not match frozen protocol")
    expected = len(protocol["fixtureIds"]) * protocol["replicatesPerFixture"] \
        * len(protocol["coordinatorTreatments"])
    if not isinstance(plan["jobs"], list) or len(plan["jobs"]) != expected:
        raise ValidationError("plan does not contain the complete cohort")
    treatments = {item["id"]: item for item in protocol["coordinatorTreatments"]}
    run_ids: list[str] = []
    blocks: dict[tuple[str, int], list[dict[str, Any]]] = {}
    machine_positions = {machine: [] for machine in protocol["machines"]}
    for index, job in enumerate(plan["jobs"]):
        _exact(job, JOB_KEYS, f"$plan.jobs[{index}]")
        treatment = treatments.get(job["coordinatorTreatmentId"])
        if treatment is None or job["coordinatorModel"] != treatment["model"] \
                or job["coordinatorReasoningEffort"] != treatment["reasoningEffort"]:
            raise ValidationError("job coordinator treatment drift")
        if job["fixtureId"] not in protocol["fixtureIds"] \
                or job["machineId"] not in protocol["machines"] \
                or not 1 <= job["replicate"] <= protocol["replicatesPerFixture"] \
                or job["workerPolicyHash"] != plan["workerPolicyHash"]:
            raise ValidationError("job references unfrozen inputs")
        if not isinstance(job["runId"], str) or not job["runId"].startswith("coord-") \
                or len(job["runId"]) != 30:
            raise ValidationError("job run ID is not opaque")
        run_ids.append(job["runId"])
        blocks.setdefault((job["fixtureId"], job["replicate"]), []).append(job)
        machine_positions[job["machineId"]].append(job["orderPosition"])
    if len(run_ids) != len(set(run_ids)):
        raise ValidationError("duplicate run IDs")
    expected_treatments = set(treatments)
    if any({job["coordinatorTreatmentId"] for job in jobs} != expected_treatments for jobs in blocks.values()):
        raise ValidationError("each fixture/replicate block must contain every coordinator treatment")
    for positions in machine_positions.values():
        if sorted(positions) != list(range(1, len(positions) + 1)):
            raise ValidationError("machine physical order is not contiguous")
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    plan_parser.add_argument("--id-key-file", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    catalog = routing_tasks.load_catalog()
    protocol = validate_protocol(load_json(args.protocol), catalog=catalog)
    if args.command == "validate":
        print(json.dumps({"valid": True, "protocolHash": value_hash(protocol)}))
        return 0
    plan = make_plan(protocol, args.id_key_file.read_bytes())
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"jobs": len(plan["jobs"]), "planHash": value_hash(plan)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
