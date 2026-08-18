#!/usr/bin/env python3
"""Preregister, plan, and analyze the narrowed routing campaign.

The independent generation is the sampling unit.  Fixtures are the
generalization unit, so every reported family rate gives each fixture equal
weight regardless of the number of observed runs.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import random
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = ROOT / "protocols" / "routing-v1.json"
DEFAULT_MATRIX = ROOT / "matrix.json"
DEFAULT_CATALOG = ROOT / "fixtures" / "catalog.json"
ALLOWED_DECISIONS = {"SUPPORTED", "INCONCLUSIVE", "CONTRADICTED"}
ALLOWED_STATUSES = {"PASS", "CANDIDATE_FAILURE", "INFRA_FAILURE"}


class ValidationError(ValueError):
    """Raised when a protocol, plan, result, or analysis is not canonical."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def seeded_order(values: Iterable[Any], seed: str) -> list[Any]:
    return sorted(
        values,
        key=lambda value: hashlib.sha256(
            f"{seed}:{canonical_json(value)}".encode("utf-8")
        ).hexdigest(),
    )


def _exact_keys(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValidationError(f"{path} must contain exactly {sorted(keys)}; got {actual}")
    return value


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{path} must be a non-empty string")
    return value


def _probability(value: Any, path: str, *, inclusive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{path} must be numeric")
    parsed = float(value)
    valid = 0 <= parsed <= 1 if inclusive else 0 < parsed < 1
    if not valid:
        raise ValidationError(f"{path} must be {'between 0 and 1' if inclusive else 'strictly between 0 and 1'}")
    return parsed


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_protocol(protocol: Any) -> dict[str, Any]:
    protocol = _exact_keys(
        protocol,
        {
            "schemaVersion", "recordKind", "protocolId", "runtimeManifestHash", "seed", "bootstrapSeed",
            "bootstrapSamples", "familywiseAlpha", "replicatesPerFixture", "machines",
            "matrix", "families", "robustness",
        },
        "$",
    )
    if protocol["schemaVersion"] != 1 or protocol["recordKind"] != "routing-protocol":
        raise ValidationError("Unsupported routing protocol identity")
    for field in ("protocolId", "seed", "bootstrapSeed"):
        _nonempty_string(protocol[field], f"$.{field}")
    runtime_hash = protocol["runtimeManifestHash"]
    if not isinstance(runtime_hash, str) or len(runtime_hash) != 64 \
            or any(character not in "0123456789abcdef" for character in runtime_hash):
        raise ValidationError("$.runtimeManifestHash must be a canonical SHA-256")
    if not isinstance(protocol["bootstrapSamples"], int) or isinstance(protocol["bootstrapSamples"], bool) \
            or protocol["bootstrapSamples"] < 100:
        raise ValidationError("$.bootstrapSamples must be an integer of at least 100")
    if not isinstance(protocol["replicatesPerFixture"], int) \
            or isinstance(protocol["replicatesPerFixture"], bool) \
            or protocol["replicatesPerFixture"] <= 0:
        raise ValidationError("$.replicatesPerFixture must be a positive integer")
    _probability(protocol["familywiseAlpha"], "$.familywiseAlpha")
    robustness = _exact_keys(protocol["robustness"], {
        "maximumQualityFloorShortfall", "maximumDecisionGainShortfall",
        "maximumFixtureCandidateRateRange", "maximumFixtureDecisionGainRange",
    }, "$.robustness")
    for field, value in robustness.items():
        _probability(value, f"$.robustness.{field}", inclusive=True)

    machines = protocol["machines"]
    if not isinstance(machines, list) or not machines:
        raise ValidationError("$.machines must be a non-empty array")
    if any(not isinstance(machine, str) or not machine.strip() for machine in machines) \
            or len(machines) != len(set(machines)):
        raise ValidationError("$.machines must contain unique non-empty strings")
    if protocol["replicatesPerFixture"] % len(machines):
        raise ValidationError("Replicates per fixture must be divisible by machine count")

    matrix = protocol["matrix"]
    if not isinstance(matrix, list) or len(matrix) != 6:
        raise ValidationError("$.matrix must contain exactly six treatments")
    treatment_keys = {"id", "model", "reasoningEffort"}
    treatment_ids: list[str] = []
    treatment_pairs: list[tuple[str, str]] = []
    for index, treatment in enumerate(matrix):
        treatment = _exact_keys(treatment, treatment_keys, f"$.matrix[{index}]")
        for field in treatment_keys:
            _nonempty_string(treatment[field], f"$.matrix[{index}].{field}")
        if treatment["reasoningEffort"] not in {"low", "medium", "high"}:
            raise ValidationError("Unsupported reasoning effort")
        treatment_ids.append(treatment["id"])
        treatment_pairs.append((treatment["model"], treatment["reasoningEffort"]))
    if len(set(treatment_ids)) != 6 or len(set(treatment_pairs)) != 6:
        raise ValidationError("Treatment IDs and model/effort pairs must be unique")

    families = protocol["families"]
    if not isinstance(families, list) or len(families) != 6:
        raise ValidationError("$.families must contain exactly six narrowed families")
    family_keys = {
        "id", "catalogFamilyId", "label", "candidateId", "heldOutFixtureIds", "absoluteQualityFloor",
        "nonInferiorityMargin", "recommendationType", "decisionComparatorId",
        "minimumDecisionGain", "heldOutFixtureEcosystems",
    }
    family_ids: list[str] = []
    candidate_ids: list[str] = []
    all_fixtures: list[str] = []
    for index, family in enumerate(families):
        family = _exact_keys(family, family_keys, f"$.families[{index}]")
        for field in (
            "id", "catalogFamilyId", "label", "candidateId", "recommendationType",
            "decisionComparatorId",
        ):
            _nonempty_string(family[field], f"$.families[{index}].{field}")
        if family["candidateId"] not in treatment_ids:
            raise ValidationError(f"Unknown candidate treatment in family {family['id']}")
        if family["recommendationType"] not in {"economy", "capability"}:
            raise ValidationError(f"Invalid recommendation type in family {family['id']}")
        if family["decisionComparatorId"] not in treatment_ids \
                or family["decisionComparatorId"] == family["candidateId"]:
            raise ValidationError(f"Invalid decision comparator in family {family['id']}")
        _probability(family["absoluteQualityFloor"], f"$.families[{index}].absoluteQualityFloor")
        _probability(family["nonInferiorityMargin"], f"$.families[{index}].nonInferiorityMargin")
        gain = family["minimumDecisionGain"]
        if not isinstance(gain, (int, float)) or isinstance(gain, bool) or not 0 < gain < 1:
            raise ValidationError("minimumDecisionGain must be strictly between 0 and 1")
        fixtures = family["heldOutFixtureIds"]
        if not isinstance(fixtures, list) or not fixtures:
            raise ValidationError(f"Family {family['id']} has no held-out fixtures")
        if any(not isinstance(item, str) or not item.strip() for item in fixtures) \
                or len(fixtures) != len(set(fixtures)):
            raise ValidationError(f"Family {family['id']} fixture IDs must be unique non-empty strings")
        ecosystems = family["heldOutFixtureEcosystems"]
        if not isinstance(ecosystems, list) or len(ecosystems) != len(fixtures) \
                or any(not isinstance(item, str) or not item.strip() for item in ecosystems):
            raise ValidationError(f"Family {family['id']} fixture ecosystems must align with fixtures")
        if len(set(ecosystems)) < 3:
            raise ValidationError(f"Family {family['id']} needs at least three ecosystems")
        blocks_per_machine = len(fixtures) * protocol["replicatesPerFixture"] // len(machines)
        if blocks_per_machine % len(matrix):
            raise ValidationError(
                f"Family {family['id']} cannot balance six treatment orders on every machine"
            )
        family_ids.append(family["id"])
        candidate_ids.append(family["candidateId"])
        all_fixtures.extend(fixtures)
    if len(family_ids) != len(set(family_ids)):
        raise ValidationError("Family IDs must be unique")
    if set(candidate_ids) != set(treatment_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise ValidationError("The six narrowed families must preregister each treatment exactly once")
    if len(all_fixtures) != len(set(all_fixtures)):
        raise ValidationError("Fixture IDs must be globally unique")

    return protocol


def validate_protocol_sources(
    protocol: dict[str, Any], matrix_document: Any, catalog: Any
) -> None:
    """Bind the frozen protocol to the checked-in treatment matrix and task catalog."""
    validate_protocol(protocol)
    if not isinstance(matrix_document, dict) or set(matrix_document) != {"configurations"} \
            or matrix_document["configurations"] != protocol["matrix"]:
        raise ValidationError("Protocol matrix does not exactly match matrix.json")
    if not isinstance(catalog, dict) or catalog.get("schemaVersion") != 2 \
            or not isinstance(catalog.get("tasks"), list):
        raise ValidationError("Routing task catalog has an unsupported shape")
    task_ids = [task.get("id") for task in catalog["tasks"] if isinstance(task, dict)]
    if len(task_ids) != len(set(task_ids)):
        raise ValidationError("Routing task catalog contains duplicate IDs")
    by_id = {
        task["id"]: task for task in catalog["tasks"]
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    expected_held_out: set[str] = set()
    for family in protocol["families"]:
        for fixture_id, ecosystem in zip(
            family["heldOutFixtureIds"], family["heldOutFixtureEcosystems"]
        ):
            task = by_id.get(fixture_id)
            if task is None or task.get("kind") != "confirmatory" \
                    or task.get("development") is not False \
                    or task.get("family") != family["catalogFamilyId"]:
                raise ValidationError(
                    f"Protocol fixture {fixture_id} is absent or mismatched in catalog.json"
                )
            if task.get("ecosystem") != ecosystem:
                raise ValidationError(
                    f"Protocol fixture {fixture_id} ecosystem differs from catalog.json"
                )
            expected_held_out.add(fixture_id)
    catalog_confirmatory = {
        task["id"] for task in catalog["tasks"]
        if isinstance(task, dict) and task.get("kind") == "confirmatory"
    }
    if catalog_confirmatory != expected_held_out:
        raise ValidationError("Protocol and catalog confirmatory fixture sets differ")


def williams_rows(treatments: list[dict[str, str]], seed: str) -> list[list[dict[str, str]]]:
    permuted = seeded_order(treatments, f"{seed}:treatments")
    count = len(permuted)
    if count % 2:
        raise ValidationError("Williams design requires an even number of treatments")
    indices = [0]
    for offset in range(1, count // 2 + 1):
        indices.append(offset)
        if count - offset != offset:
            indices.append(count - offset)
    base = indices[:count]
    return [[permuted[(index + row) % count] for index in base] for row in range(count)]


def _scheduled_jobs(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    rows = williams_rows(protocol["matrix"], protocol["seed"])
    machine_count = len(protocol["machines"])
    row_count = len(rows)
    for family in protocol["families"]:
        blocks_by_machine: dict[str, list[tuple[str, int]]] = {
            machine: [] for machine in protocol["machines"]
        }
        fixture_order = seeded_order(
            family["heldOutFixtureIds"], f"{protocol['seed']}:{family['id']}:fixtures"
        )
        for fixture_id in fixture_order:
            machine_order = seeded_order(
                protocol["machines"],
                f"{protocol['seed']}:{family['id']}:{fixture_id}:machines",
            )
            for replicate in range(protocol["replicatesPerFixture"]):
                machine = machine_order[replicate % machine_count]
                blocks_by_machine[machine].append((fixture_id, replicate))

        assigned_blocks: list[dict[str, Any]] = []
        for machine in protocol["machines"]:
            blocks = seeded_order(
                blocks_by_machine[machine],
                f"{protocol['seed']}:{family['id']}:{machine}:blocks",
            )
            if len(blocks) % row_count:
                raise ValidationError(
                    f"Family {family['id']} assigns {len(blocks)} blocks to {machine}; "
                    f"a multiple of {row_count} is required for exact order balance"
                )
            row_order = seeded_order(
                list(range(row_count)), f"{protocol['seed']}:{family['id']}:{machine}:rows"
            )
            for block_index, (fixture_id, replicate) in enumerate(blocks):
                assigned_blocks.append({
                    "fixtureId": fixture_id,
                    "replicate": replicate,
                    "machineId": machine,
                    "row": row_order[block_index % row_count],
                })
        assigned_blocks = seeded_order(
            assigned_blocks, f"{protocol['seed']}:{family['id']}:physical-order"
        )
        for block_index, block in enumerate(assigned_blocks):
            for position, treatment in enumerate(rows[block["row"]]):
                jobs.append({
                    "familyId": family["id"],
                    "fixtureId": block["fixtureId"],
                    "replicate": block["replicate"],
                    "block": block_index,
                    "orderPosition": position,
                    "machineId": block["machineId"],
                    "treatmentId": treatment["id"],
                    "model": treatment["model"],
                    "reasoningEffort": treatment["reasoningEffort"],
                })
    return jobs


def validate_preflight_reports(
    reports: list[Any], protocol: dict[str, Any], runtime_manifest: dict[str, Any]
) -> list[dict[str, str]]:
    """Validate one preregistration-reversing capability report per machine."""
    import routing_preflight  # lazy import avoids module initialization cycle

    if not isinstance(reports, list) or len(reports) != len(protocol["machines"]):
        raise ValidationError("Plan requires exactly one preflight report per machine")
    by_machine: dict[str, dict[str, Any]] = {}
    try:
        for report in reports:
            validated = routing_preflight.validate_report(report, protocol, runtime_manifest)
            machine_id = validated["machineId"]
            if machine_id in by_machine:
                raise ValidationError(f"Duplicate preflight report for {machine_id}")
            by_machine[machine_id] = validated
    except routing_preflight.PreflightError as error:
        raise ValidationError(str(error)) from error
    if set(by_machine) != set(protocol["machines"]):
        raise ValidationError("Preflight reports do not exactly cover protocol machines")
    capability_digests = {report["capabilityDigest"] for report in by_machine.values()}
    if len(capability_digests) != 1:
        raise ValidationError("Campaign machines advertise different frozen capabilities")
    return [{
        "machineId": machine_id,
        "reportHash": value_hash(by_machine[machine_id]),
        "capabilityDigest": by_machine[machine_id]["capabilityDigest"],
    } for machine_id in protocol["machines"]]


def make_plan(
    protocol: dict[str, Any], id_key: bytes, preflight_reports: list[Any],
    runtime_manifest: dict[str, Any],
) -> dict[str, Any]:
    validate_protocol(protocol)
    if len(id_key) < 16:
        raise ValidationError("HMAC ID key must contain at least 16 bytes")
    protocol_hash = value_hash(protocol)
    bindings = validate_preflight_reports(preflight_reports, protocol, runtime_manifest)
    jobs = []
    for scheduled in _scheduled_jobs(protocol):
        message = ":".join((
            protocol_hash, protocol["protocolId"], scheduled["familyId"],
            scheduled["fixtureId"], str(scheduled["replicate"]), scheduled["treatmentId"],
        )).encode("utf-8")
        run_id = hmac.new(id_key, message, hashlib.sha256).hexdigest()[:16]
        jobs.append({"runId": run_id, **scheduled})
    plan = {
        "schemaVersion": 2,
        "recordKind": "routing-plan",
        "protocolId": protocol["protocolId"],
        "protocolHash": protocol_hash,
        "seed": protocol["seed"],
        "preflightBindings": bindings,
        "jobs": jobs,
    }
    validate_plan(plan, protocol)
    return plan


PLAN_KEYS = {
    "schemaVersion", "recordKind", "protocolId", "protocolHash", "seed",
    "preflightBindings", "jobs",
}
JOB_KEYS = {
    "runId", "familyId", "fixtureId", "replicate", "block", "orderPosition",
    "machineId", "treatmentId", "model", "reasoningEffort",
}


def validate_plan(plan: Any, protocol: dict[str, Any]) -> dict[str, Any]:
    validate_protocol(protocol)
    plan = _exact_keys(plan, PLAN_KEYS, "$plan")
    if plan["schemaVersion"] != 2 or plan["recordKind"] != "routing-plan":
        raise ValidationError("Unsupported routing plan identity")
    if plan["protocolId"] != protocol["protocolId"] \
            or plan["protocolHash"] != value_hash(protocol) or plan["seed"] != protocol["seed"]:
        raise ValidationError("Plan does not match protocol")
    bindings = plan["preflightBindings"]
    if not isinstance(bindings, list) or len(bindings) != len(protocol["machines"]):
        raise ValidationError("Plan preflight bindings do not cover protocol machines")
    expected_machine_order = protocol["machines"]
    capability_digests: set[str] = set()
    for index, binding in enumerate(bindings):
        binding = _exact_keys(
            binding, {"machineId", "reportHash", "capabilityDigest"},
            f"$plan.preflightBindings[{index}]",
        )
        if binding["machineId"] != expected_machine_order[index]:
            raise ValidationError("Plan preflight bindings are not in protocol machine order")
        for field in ("reportHash", "capabilityDigest"):
            value = binding[field]
            if not isinstance(value, str) or len(value) != 64 \
                    or any(character not in "0123456789abcdef" for character in value):
                raise ValidationError(f"Plan preflight {field} is not a canonical SHA-256")
        capability_digests.add(binding["capabilityDigest"])
    if len(capability_digests) != 1:
        raise ValidationError("Plan machine capability digests differ")
    expected_count = sum(
        len(family["heldOutFixtureIds"]) * protocol["replicatesPerFixture"] * len(protocol["matrix"])
        for family in protocol["families"]
    )
    if not isinstance(plan["jobs"], list) or len(plan["jobs"]) != expected_count:
        raise ValidationError("Plan job count does not cover the protocol")
    treatment_by_id = {item["id"]: item for item in protocol["matrix"]}
    family_by_id = {item["id"]: item for item in protocol["families"]}
    run_ids: list[str] = []
    blocks: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for index, job in enumerate(plan["jobs"]):
        job = _exact_keys(job, JOB_KEYS, f"$plan.jobs[{index}]")
        if not isinstance(job["runId"], str) or len(job["runId"]) != 16 \
                or any(character not in "0123456789abcdef" for character in job["runId"]):
            raise ValidationError("Run IDs must be opaque 16-character lowercase hex strings")
        family = family_by_id.get(job["familyId"])
        treatment = treatment_by_id.get(job["treatmentId"])
        if family is None or job["fixtureId"] not in family["heldOutFixtureIds"]:
            raise ValidationError("Plan job references an unknown family fixture")
        if treatment is None or any(job[field] != treatment[field] for field in ("model", "reasoningEffort")):
            raise ValidationError("Plan job treatment does not match protocol matrix")
        if job["machineId"] not in protocol["machines"]:
            raise ValidationError("Plan job references an unknown machine")
        for field in ("replicate", "block", "orderPosition"):
            if not isinstance(job[field], int) or isinstance(job[field], bool) or job[field] < 0:
                raise ValidationError(f"Plan {field} must be a non-negative integer")
        if job["replicate"] >= protocol["replicatesPerFixture"]:
            raise ValidationError("Plan replicate exceeds protocol")
        run_ids.append(job["runId"])
        blocks.setdefault((job["familyId"], job["fixtureId"], job["replicate"]), []).append(job)
    if len(run_ids) != len(set(run_ids)):
        raise ValidationError("Plan contains duplicate run IDs")
    structural_jobs = [
        {key: value for key, value in job.items() if key != "runId"}
        for job in plan["jobs"]
    ]
    if structural_jobs != _scheduled_jobs(protocol):
        raise ValidationError("Plan jobs do not match the seed-derived schedule")
    expected_treatments = set(treatment_by_id)
    for block in blocks.values():
        if len(block) != len(expected_treatments) or {job["treatmentId"] for job in block} != expected_treatments:
            raise ValidationError("Every fixture replicate must contain every treatment once")
        if sorted(job["orderPosition"] for job in block) != list(range(len(expected_treatments))):
            raise ValidationError("Every block must contain each order position once")
        if len({job["machineId"] for job in block}) != 1:
            raise ValidationError("Every block must run on one machine")
    for family in protocol["families"]:
        for machine in protocol["machines"]:
            for treatment in protocol["matrix"]:
                counts = {
                    position: sum(
                        job["familyId"] == family["id"] and job["machineId"] == machine
                        and job["treatmentId"] == treatment["id"]
                        and job["orderPosition"] == position
                        for job in plan["jobs"]
                    )
                    for position in range(len(protocol["matrix"]))
                }
                if len(set(counts.values())) != 1:
                    raise ValidationError(
                        "Treatment order is not balanced within family and machine"
                    )
    return plan


RESULT_KEYS = {
    "schemaVersion", "recordKind", "protocolHash", "planHash", "runId", "familyId",
    "fixtureId", "replicate", "machineId", "orderPosition", "treatmentId", "model",
    "reasoningEffort", "status", "generationDurationSeconds", "evaluationDurationSeconds",
}
RESULT_V2_KEYS = {
    "schemaVersion", "recordKind", "protocolHash", "planHash", "runtimeManifestHash",
    "preflightReportHash", "capabilityDigest",
    "runId", "familyId", "fixtureId", "replicate", "machineId", "orderPosition",
    "treatmentId", "model", "reasoningEffort", "status", "failureKind",
    "fixtureManifestHash", "promptHash", "candidateHash", "transcriptHash", "generation",
    "usage", "evaluation", "generationDurationSeconds", "evaluationDurationSeconds",
    "totalDurationSeconds", "provenance",
}


def validate_results(
    results: list[Any], plan: dict[str, Any], protocol: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    validate_plan(plan, protocol)
    jobs = {job["runId"]: job for job in plan["jobs"]}
    seen: dict[str, dict[str, Any]] = {}
    plan_hash = value_hash(plan)
    for index, result in enumerate(results):
        version = result.get("schemaVersion") if isinstance(result, dict) else None
        result = _exact_keys(
            result, RESULT_V2_KEYS if version == 2 else RESULT_KEYS, f"$results[{index}]"
        )
        if version not in {1, 2} or result["recordKind"] != "routing-result":
            raise ValidationError("Unsupported routing result identity")
        run_id = result["runId"]
        if run_id in seen:
            raise ValidationError(f"Duplicate routing result {run_id}")
        job = jobs.get(run_id)
        if job is None:
            raise ValidationError(f"Unplanned routing result {run_id}")
        if result["protocolHash"] != value_hash(protocol) or result["planHash"] != plan_hash:
            raise ValidationError(f"Result hash mismatch for {run_id}")
        if version == 2:
            for field in (
                "runtimeManifestHash", "preflightReportHash", "capabilityDigest",
                "fixtureManifestHash", "promptHash", "transcriptHash",
            ):
                value = result[field]
                if not isinstance(value, str) or len(value) != 64 \
                        or any(character not in "0123456789abcdef" for character in value):
                    raise ValidationError(f"Invalid {field} for {run_id}")
            if result["runtimeManifestHash"] != protocol["runtimeManifestHash"]:
                raise ValidationError(f"Runtime manifest mismatch for {run_id}")
            binding = next((
                item for item in plan["preflightBindings"]
                if item["machineId"] == result["machineId"]
            ), None)
            if binding is None:
                raise ValidationError(f"Unknown preflight machine for {run_id}")
            if result["preflightReportHash"] != binding["reportHash"] \
                    or result["capabilityDigest"] != binding["capabilityDigest"]:
                raise ValidationError(f"Preflight binding mismatch for {run_id}")
            generation = _exact_keys(result["generation"], {
                "backend", "isolation", "generatorImage", "codexVersion", "attemptHash", "requestedModel",
                "requestedReasoningEffort", "requestedServiceTier", "fastMode", "multiAgent",
                "observedModel", "serviceTier", "runtimeVerification",
            }, f"$results[{index}].generation")
            if generation["requestedModel"] != result["model"] \
                    or generation["requestedReasoningEffort"] != result["reasoningEffort"] \
                    or generation["fastMode"] is not (
                        generation["requestedServiceTier"] == "priority"
                    ) \
                    or generation["multiAgent"] is not False:
                raise ValidationError(f"Generator controls mismatch for {run_id}")
            if generation["runtimeVerification"] not in {
                "telemetry-confirmed", "cli-request-and-success",
            }:
                raise ValidationError(f"Invalid runtime verification for {run_id}")
            if result["status"] != "INFRA_FAILURE" and (
                (
                    generation["observedModel"] is not None
                    and generation["observedModel"] != result["model"]
                )
                or (
                    generation["serviceTier"] is not None
                    and generation["serviceTier"] != generation["requestedServiceTier"]
                )
            ):
                raise ValidationError(f"Unresolved runtime drift for {run_id}")
            _exact_keys(result["usage"], {
                "inputTokens", "cachedInputTokens", "outputTokens",
                "reasoningOutputTokens", "cacheWriteInputTokens", "totalTokens",
            }, f"$results[{index}].usage")
            _exact_keys(result["evaluation"], {
                "backend", "evaluatorProfile", "evaluatorImage", "reportHash",
            }, f"$results[{index}].evaluation")
        for field in (
            "familyId", "fixtureId", "replicate", "machineId", "orderPosition",
            "treatmentId", "model", "reasoningEffort",
        ):
            if result[field] != job[field]:
                raise ValidationError(f"Result {field} does not match plan for {run_id}")
        if result["status"] not in ALLOWED_STATUSES:
            raise ValidationError(f"Invalid status for {run_id}")
        for field in (
            "generationDurationSeconds", "evaluationDurationSeconds",
            *( ["totalDurationSeconds"] if version == 2 else [] ),
        ):
            value = result[field]
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(float(value)) or value < 0
            ):
                raise ValidationError(f"Invalid {field} for {run_id}")
        if result["generationDurationSeconds"] is None:
            raise ValidationError(f"Generation duration is required for {run_id}")
        seen[run_id] = result
    return seen


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValidationError("Cannot compute a percentile of no values")
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def holm_adjust(p_values: list[float]) -> list[float]:
    count = len(p_values)
    ordered = sorted(range(count), key=lambda index: (p_values[index], index))
    adjusted = [0.0] * count
    running = 0.0
    for rank, index in enumerate(ordered):
        candidate = min(1.0, (count - rank) * p_values[index])
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


def centered_bootstrap_p(
    draws: list[float], estimate: float, threshold: float, alternative: str
) -> float:
    """Return a finite-sample corrected, null-centered bootstrap tail probability.

    Bootstrap draws estimate sampling variation around ``estimate``.  Testing at
    the boundary ``threshold`` therefore compares the observed displacement
    from that boundary with centered bootstrap deviations.  Counting raw draws
    on the other side of the threshold would not be a hypothesis test and can
    badly misstate evidence when the estimate is far from the null boundary.
    """
    if not draws:
        raise ValidationError("Centered bootstrap test requires draws")
    observed = estimate - threshold
    deviations = [draw - estimate for draw in draws]
    if alternative == "greater":
        extreme = sum(deviation >= observed for deviation in deviations)
    elif alternative == "less":
        extreme = sum(deviation <= observed for deviation in deviations)
    else:
        raise ValidationError("Bootstrap alternative must be greater or less")
    return (1 + extreme) / (len(draws) + 1)


def _fixture_equal_rate(
    grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
    fixtures: list[str], treatment_id: str,
) -> float:
    rates = []
    for fixture in fixtures:
        outcomes = [
            grouped[fixture][replicate][treatment_id]["status"] == "PASS"
            for replicate in sorted(grouped[fixture])
        ]
        rates.append(sum(outcomes) / len(outcomes))
    return sum(rates) / len(rates)


def _fixture_equal_duration(
    grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
    fixtures: list[str], treatment_id: str,
) -> float:
    fixture_means = []
    for fixture in fixtures:
        durations = [
            float(grouped[fixture][replicate][treatment_id]["generationDurationSeconds"])
            for replicate in sorted(grouped[fixture])
        ]
        fixture_means.append(sum(durations) / len(durations))
    return sum(fixture_means) / len(fixture_means)


def _bootstrap_family(
    grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
    fixtures: list[str], candidate_id: str, comparator_ids: list[str],
    decision_comparator_id: str, samples: int, seed: str,
) -> tuple[list[float], dict[str, list[float]], list[float]]:
    rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16))
    absolute: list[float] = []
    contrasts = {comparator: [] for comparator in comparator_ids}
    efficiency: list[float] = []
    for _ in range(samples):
        candidate_fixture_rates: list[float] = []
        comparator_fixture_rates = {comparator: [] for comparator in comparator_ids}
        candidate_fixture_durations: list[float] = []
        efficiency_fixture_durations: list[float] = []
        for _fixture_draw in fixtures:
            fixture = fixtures[rng.randrange(len(fixtures))]
            replicates = sorted(grouped[fixture])
            drawn = [replicates[rng.randrange(len(replicates))] for _ in replicates]
            candidate_fixture_rates.append(sum(
                grouped[fixture][replicate][candidate_id]["status"] == "PASS"
                for replicate in drawn
            ) / len(drawn))
            for comparator in comparator_ids:
                comparator_fixture_rates[comparator].append(sum(
                    grouped[fixture][replicate][comparator]["status"] == "PASS"
                    for replicate in drawn
                ) / len(drawn))
            candidate_fixture_durations.append(sum(
                float(grouped[fixture][replicate][candidate_id]["generationDurationSeconds"])
                for replicate in drawn
            ) / len(drawn))
            efficiency_fixture_durations.append(sum(
                float(grouped[fixture][replicate][decision_comparator_id]["generationDurationSeconds"])
                for replicate in drawn
            ) / len(drawn))
        candidate_rate = sum(candidate_fixture_rates) / len(candidate_fixture_rates)
        absolute.append(candidate_rate)
        for comparator in comparator_ids:
            comparison_rate = sum(comparator_fixture_rates[comparator]) / len(fixtures)
            contrasts[comparator].append(candidate_rate - comparison_rate)
        candidate_seconds = sum(candidate_fixture_durations) / len(fixtures)
        comparator_seconds = sum(efficiency_fixture_durations) / len(fixtures)
        efficiency.append(1 - candidate_seconds / comparator_seconds if comparator_seconds else -1.0)
    return absolute, contrasts, efficiency


def _stratum_estimate(
    grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
    fixtures: list[str], candidate_id: str, comparator_id: str,
    recommendation_type: str, *, machine_id: str | None = None,
) -> dict[str, float | int]:
    candidate_outcomes: list[bool] = []
    comparator_outcomes: list[bool] = []
    candidate_durations: list[float] = []
    comparator_durations: list[float] = []
    for fixture in fixtures:
        for replicate in sorted(grouped[fixture]):
            block = grouped[fixture][replicate]
            if machine_id is not None and block[candidate_id]["machineId"] != machine_id:
                continue
            candidate_outcomes.append(block[candidate_id]["status"] == "PASS")
            comparator_outcomes.append(block[comparator_id]["status"] == "PASS")
            candidate_durations.append(float(block[candidate_id]["generationDurationSeconds"]))
            comparator_durations.append(float(block[comparator_id]["generationDurationSeconds"]))
    if not candidate_outcomes:
        raise ValidationError("Robustness stratum contains no observations")
    candidate_rate = sum(candidate_outcomes) / len(candidate_outcomes)
    comparator_rate = sum(comparator_outcomes) / len(comparator_outcomes)
    candidate_seconds = sum(candidate_durations) / len(candidate_durations)
    comparator_seconds = sum(comparator_durations) / len(comparator_durations)
    if recommendation_type == "economy":
        candidate_value, comparator_value = candidate_seconds, comparator_seconds
        gain = 1 - candidate_seconds / comparator_seconds if comparator_seconds else -1.0
    else:
        candidate_value, comparator_value = candidate_rate, comparator_rate
        gain = candidate_rate - comparator_rate
    return {
        "fixtures": len(fixtures), "observations": len(candidate_outcomes),
        "candidatePassRate": candidate_rate, "comparatorPassRate": comparator_rate,
        "candidateValue": candidate_value, "comparatorValue": comparator_value,
        "gain": gain,
    }


def _robustness_analysis(
    protocol: dict[str, Any], family: dict[str, Any],
    grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
) -> dict[str, Any]:
    fixtures = family["heldOutFixtureIds"]
    candidate = family["candidateId"]
    comparator = family["decisionComparatorId"]
    kind = family["recommendationType"]
    machines = []
    for machine in protocol["machines"]:
        estimate = _stratum_estimate(
            grouped, fixtures, candidate, comparator, kind, machine_id=machine
        )
        machines.append({"id": machine, **estimate})
    ecosystem_groups: dict[str, list[str]] = {}
    for fixture, ecosystem in zip(fixtures, family["heldOutFixtureEcosystems"]):
        ecosystem_groups.setdefault(ecosystem, []).append(fixture)
    ecosystems = [
        {"id": ecosystem, **_stratum_estimate(
            grouped, members, candidate, comparator, kind
        )}
        for ecosystem, members in sorted(ecosystem_groups.items())
    ]
    leave_one_out = [
        {"omittedFixtureId": omitted, **_stratum_estimate(
            grouped, [fixture for fixture in fixtures if fixture != omitted],
            candidate, comparator, kind,
        )}
        for omitted in fixtures
    ]
    fixture_estimates = [
        {"fixtureId": fixture, **_stratum_estimate(
            grouped, [fixture], candidate, comparator, kind
        )}
        for fixture in fixtures
    ]
    fixture_rates = [item["candidatePassRate"] for item in fixture_estimates]
    fixture_gains = [item["gain"] for item in fixture_estimates]
    heterogeneity = {
        "fixtureCandidateRateRange": max(fixture_rates) - min(fixture_rates),
        "fixtureDecisionGainRange": max(fixture_gains) - min(fixture_gains),
    }
    thresholds = protocol["robustness"]
    quality_boundary = family["absoluteQualityFloor"] - thresholds["maximumQualityFloorShortfall"]
    gain_boundary = family["minimumDecisionGain"] - thresholds["maximumDecisionGainShortfall"]
    draft = {
        "thresholds": {
            **thresholds, "qualityBoundary": quality_boundary,
            "decisionGainBoundary": gain_boundary,
        },
        "machines": machines, "ecosystems": ecosystems,
        "leaveOneFixtureOut": leave_one_out, "fixtures": fixture_estimates,
        "heterogeneity": heterogeneity,
    }
    reasons = _robustness_reasons(draft)
    return {**draft, "passed": not reasons, "reasons": reasons}


def _robustness_reasons(robustness: dict[str, Any]) -> list[str]:
    thresholds = robustness["thresholds"]
    machines = robustness["machines"]
    ecosystems = robustness["ecosystems"]
    leave_one_out = robustness["leaveOneFixtureOut"]
    heterogeneity = robustness["heterogeneity"]
    quality_boundary = thresholds["qualityBoundary"]
    gain_boundary = thresholds["decisionGainBoundary"]
    reasons: list[str] = []
    if any(item["candidatePassRate"] < quality_boundary for item in machines):
        reasons.append("machine-quality-instability")
    if any(item["gain"] < gain_boundary for item in machines):
        reasons.append("machine-gain-instability")
    if any(item["candidatePassRate"] < quality_boundary for item in ecosystems):
        reasons.append("ecosystem-quality-instability")
    if any(item["gain"] < gain_boundary for item in ecosystems):
        reasons.append("ecosystem-gain-instability")
    if any(item["gain"] < gain_boundary for item in leave_one_out):
        reasons.append("leave-one-fixture-out-instability")
    if heterogeneity["fixtureCandidateRateRange"] > thresholds["maximumFixtureCandidateRateRange"]:
        reasons.append("fixture-quality-heterogeneity")
    if heterogeneity["fixtureDecisionGainRange"] > thresholds["maximumFixtureDecisionGainRange"]:
        reasons.append("fixture-gain-heterogeneity")
    return reasons


def _classify_family_analysis(
    family: dict[str, Any], alpha: float
) -> tuple[str, list[str]]:
    absolute = family["absoluteQuality"]
    decision_gate = family["decisionGate"]
    quality_supported = (
        absolute["holmAdjustedSupportP"] <= alpha
        and absolute["lower95"] > absolute["floor"]
    )
    quality_contradicted = (
        absolute["holmAdjustedContradictionP"] <= alpha
        and absolute["upper95"] < absolute["floor"]
    )
    ni_supported = all(
        item["holmAdjustedNonInferiorityP"] <= alpha
        and item["lower95"] > -item["nonInferiorityMargin"]
        for item in family["comparisons"]
    )
    ni_contradicted = any(
        item["holmAdjustedContradictionP"] <= alpha
        and item["upper95"] < -item["nonInferiorityMargin"]
        for item in family["comparisons"]
    )
    decision_supported = (
        decision_gate["holmAdjustedSupportP"] <= alpha
        and decision_gate["lower95"] > decision_gate["minimumGain"]
    )
    decision_contradicted = (
        decision_gate["holmAdjustedContradictionP"] <= alpha
        and decision_gate["upper95"] < decision_gate["minimumGain"]
    )
    robustness_supported = family["robustness"]["passed"] is True
    if quality_contradicted or ni_contradicted or decision_contradicted:
        decision = "CONTRADICTED"
    elif quality_supported and ni_supported and decision_supported and robustness_supported:
        decision = "SUPPORTED"
    else:
        decision = "INCONCLUSIVE"
    reasons = []
    if not quality_supported:
        reasons.append("absolute-quality-gate")
    if not ni_supported:
        reasons.append("noninferiority-gate")
    if not decision_supported:
        reasons.append(f"{decision_gate['type']}-gain-gate")
    if not robustness_supported:
        reasons.extend(family["robustness"]["reasons"])
    if quality_contradicted:
        reasons.append("absolute-quality-contradicted")
    if ni_contradicted:
        reasons.append("noninferiority-contradicted")
    if decision_contradicted:
        reasons.append(f"{decision_gate['type']}-gain-contradicted")
    return decision, reasons


def analyze(
    protocol: dict[str, Any], plan: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    validate_protocol(protocol)
    validate_plan(plan, protocol)
    results_by_id = validate_results(results, plan, protocol)
    missing = sorted(set(job["runId"] for job in plan["jobs"]) - set(results_by_id))
    unresolved = sorted(
        run_id for run_id, result in results_by_id.items()
        if result["status"] == "INFRA_FAILURE"
    )
    if missing or unresolved:
        raise ValidationError(
            f"Routing decisions require a complete resolved cohort; "
            f"missing={len(missing)}, unresolved={len(unresolved)}"
        )

    treatment_ids = [item["id"] for item in protocol["matrix"]]
    family_analyses: list[dict[str, Any]] = []
    support_p_values: list[float] = []
    contradiction_p_values: list[float] = []
    support_claims: list[dict[str, Any]] = []
    contradiction_claims: list[dict[str, Any]] = []
    alpha = float(protocol["familywiseAlpha"])
    bootstrap_samples = protocol["bootstrapSamples"]
    preregistered_claims = len(protocol["families"]) * (len(protocol["matrix"]) + 1)
    simultaneous_tail_alpha = alpha / preregistered_claims

    for family_index, family in enumerate(protocol["families"]):
        family_results = [
            result for result in results_by_id.values()
            if result["familyId"] == family["id"]
        ]
        grouped: dict[str, dict[int, dict[str, dict[str, Any]]]] = {
            fixture: {} for fixture in family["heldOutFixtureIds"]
        }
        for result in family_results:
            grouped[result["fixtureId"]].setdefault(result["replicate"], {})[
                result["treatmentId"]
            ] = result
        for fixture, replicates in grouped.items():
            if len(replicates) != protocol["replicatesPerFixture"] \
                    or any(set(items) != set(treatment_ids) for items in replicates.values()):
                raise ValidationError(f"Incomplete treatment block for {family['id']}:{fixture}")

        candidate = family["candidateId"]
        comparators = [item for item in treatment_ids if item != candidate]
        candidate_rate = _fixture_equal_rate(grouped, family["heldOutFixtureIds"], candidate)
        candidate_seconds = _fixture_equal_duration(grouped, family["heldOutFixtureIds"], candidate)
        decision_comparator = family["decisionComparatorId"]
        comparator_seconds = _fixture_equal_duration(
            grouped, family["heldOutFixtureIds"], decision_comparator
        )
        efficiency_gain = 1 - candidate_seconds / comparator_seconds if comparator_seconds else -1.0
        absolute_draws, contrast_draws, efficiency_draws = _bootstrap_family(
            grouped,
            family["heldOutFixtureIds"],
            candidate,
            comparators,
            decision_comparator,
            bootstrap_samples,
            f"{protocol['bootstrapSeed']}:{family['id']}",
        )
        absolute = {
            "estimate": candidate_rate,
            "nominalLower95": percentile(absolute_draws, alpha),
            "nominalUpper95": percentile(absolute_draws, 1 - alpha),
            "lower95": percentile(absolute_draws, simultaneous_tail_alpha),
            "upper95": percentile(
                absolute_draws, 1 - simultaneous_tail_alpha
            ),
            "floor": family["absoluteQualityFloor"],
            "rawSupportP": centered_bootstrap_p(
                absolute_draws, candidate_rate, float(family["absoluteQualityFloor"]),
                "greater",
            ),
            "holmAdjustedSupportP": 1.0,
            "rawContradictionP": centered_bootstrap_p(
                absolute_draws, candidate_rate, float(family["absoluteQualityFloor"]),
                "less",
            ),
            "holmAdjustedContradictionP": 1.0,
        }
        support_p_values.append(absolute["rawSupportP"])
        contradiction_p_values.append(absolute["rawContradictionP"])
        support_claims.append(absolute)
        contradiction_claims.append(absolute)
        comparisons: list[dict[str, Any]] = []
        for comparator in comparators:
            comparator_rate = _fixture_equal_rate(
                grouped, family["heldOutFixtureIds"], comparator
            )
            draws = contrast_draws[comparator]
            margin = float(family["nonInferiorityMargin"])
            difference = candidate_rate - comparator_rate
            raw_noninferiority = centered_bootstrap_p(
                draws, difference, -margin, "greater"
            )
            raw_contradiction = centered_bootstrap_p(
                draws, difference, -margin, "less"
            )
            comparisons.append({
                "comparatorId": comparator,
                "candidatePassRate": candidate_rate,
                "comparatorPassRate": comparator_rate,
                "difference": difference,
                "nominalLower95": percentile(draws, alpha),
                "nominalUpper95": percentile(draws, 1 - alpha),
                "lower95": percentile(draws, simultaneous_tail_alpha),
                "upper95": percentile(
                    draws, 1 - simultaneous_tail_alpha
                ),
                "nonInferiorityMargin": margin,
                "rawNonInferiorityP": raw_noninferiority,
                "holmAdjustedNonInferiorityP": 1.0,
                "rawContradictionP": raw_contradiction,
                "holmAdjustedContradictionP": 1.0,
            })
            support_p_values.append(raw_noninferiority)
            contradiction_p_values.append(raw_contradiction)
            support_claims.append(comparisons[-1])
            contradiction_claims.append(comparisons[-1])
        if family["recommendationType"] == "economy":
            decision_draws = efficiency_draws
            decision_estimate = efficiency_gain
            candidate_value = candidate_seconds
            comparator_value = comparator_seconds
            metric = "generation-duration-gain-fraction"
        else:
            decision_draws = contrast_draws[decision_comparator]
            decision_estimate = candidate_rate - _fixture_equal_rate(
                grouped, family["heldOutFixtureIds"], decision_comparator
            )
            candidate_value = candidate_rate
            comparator_value = _fixture_equal_rate(
                grouped, family["heldOutFixtureIds"], decision_comparator
            )
            metric = "verified-completion-rate-difference"
        decision_gate = {
            "type": family["recommendationType"],
            "metric": metric,
            "comparatorId": decision_comparator,
            "candidateValue": candidate_value,
            "comparatorValue": comparator_value,
            "gain": decision_estimate,
            "nominalLower95": percentile(decision_draws, alpha),
            "nominalUpper95": percentile(decision_draws, 1 - alpha),
            "lower95": percentile(decision_draws, simultaneous_tail_alpha),
            "upper95": percentile(
                decision_draws, 1 - simultaneous_tail_alpha
            ),
            "minimumGain": family["minimumDecisionGain"],
            "rawSupportP": centered_bootstrap_p(
                decision_draws, decision_estimate, float(family["minimumDecisionGain"]),
                "greater",
            ),
            "holmAdjustedSupportP": 1.0,
            "rawContradictionP": centered_bootstrap_p(
                decision_draws, decision_estimate, float(family["minimumDecisionGain"]),
                "less",
            ),
            "holmAdjustedContradictionP": 1.0,
        }
        support_p_values.append(decision_gate["rawSupportP"])
        contradiction_p_values.append(decision_gate["rawContradictionP"])
        support_claims.append(decision_gate)
        contradiction_claims.append(decision_gate)
        family_analyses.append({
            "familyId": family["id"],
            "candidateId": candidate,
            "plannedRuns": len(family["heldOutFixtureIds"])
            * protocol["replicatesPerFixture"] * len(treatment_ids),
            "observedRuns": len(family_results),
            "absoluteQuality": absolute,
            "comparisons": comparisons,
            "decisionGate": decision_gate,
            "robustness": _robustness_analysis(protocol, family, grouped),
            "decision": "INCONCLUSIVE",
            "reasons": [],
        })

    if len(support_p_values) != preregistered_claims \
            or len(contradiction_p_values) != preregistered_claims:
        raise ValidationError("Internal routing claim-family size mismatch")
    adjusted_support = holm_adjust(support_p_values)
    adjusted_contradiction = holm_adjust(contradiction_p_values)
    for claim, adjusted in zip(support_claims, adjusted_support):
        field = (
            "holmAdjustedNonInferiorityP"
            if "holmAdjustedNonInferiorityP" in claim
            else "holmAdjustedSupportP"
        )
        claim[field] = adjusted
    for claim, adjusted in zip(contradiction_claims, adjusted_contradiction):
        claim["holmAdjustedContradictionP"] = adjusted

    for family in family_analyses:
        family["decision"], family["reasons"] = _classify_family_analysis(family, alpha)

    analysis = {
        "schemaVersion": 2,
        "recordKind": "routing-analysis",
        "protocolId": protocol["protocolId"],
        "protocolHash": value_hash(protocol),
        "planHash": value_hash(plan),
        "complete": True,
        "bootstrap": {
            "method": "fixture-then-paired-replicate-percentile",
            "seed": protocol["bootstrapSeed"],
            "samples": bootstrap_samples,
        },
        "multiplicity": {
            "method": "holm-centered-bootstrap",
            "familywiseAlpha": alpha,
            "supportHypotheses": len(support_p_values),
            "contradictionHypotheses": len(contradiction_p_values),
            "simultaneousBoundsMethod": "bonferroni-percentile",
            "simultaneousTailAlpha": simultaneous_tail_alpha,
        },
        "families": family_analyses,
    }
    validate_analysis(analysis, protocol, plan)
    return analysis


def validate_analysis(
    analysis: Any, protocol: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    validate_protocol(protocol)
    validate_plan(plan, protocol)
    analysis = _exact_keys(
        analysis,
        {
            "schemaVersion", "recordKind", "protocolId", "protocolHash", "planHash",
            "complete", "bootstrap", "multiplicity", "families",
        },
        "$analysis",
    )
    if analysis["schemaVersion"] != 2 or analysis["recordKind"] != "routing-analysis" \
            or analysis["complete"] is not True:
        raise ValidationError("Unsupported or incomplete routing analysis")
    if analysis["protocolId"] != protocol["protocolId"] \
            or analysis["protocolHash"] != value_hash(protocol) \
            or analysis["planHash"] != value_hash(plan):
        raise ValidationError("Analysis hashes do not match protocol and plan")
    _exact_keys(analysis["bootstrap"], {"method", "seed", "samples"}, "$analysis.bootstrap")
    _exact_keys(
        analysis["multiplicity"],
        {
            "method", "familywiseAlpha", "supportHypotheses",
            "contradictionHypotheses", "simultaneousBoundsMethod",
            "simultaneousTailAlpha",
        },
        "$analysis.multiplicity",
    )
    if analysis["bootstrap"] != {
        "method": "fixture-then-paired-replicate-percentile",
        "seed": protocol["bootstrapSeed"],
        "samples": protocol["bootstrapSamples"],
    }:
        raise ValidationError("Analysis bootstrap settings do not match protocol")
    expected_hypotheses = len(protocol["families"]) * (len(protocol["matrix"]) + 1)
    if analysis["multiplicity"] != {
        "method": "holm-centered-bootstrap",
        "familywiseAlpha": float(protocol["familywiseAlpha"]),
        "supportHypotheses": expected_hypotheses,
        "contradictionHypotheses": expected_hypotheses,
        "simultaneousBoundsMethod": "bonferroni-percentile",
        "simultaneousTailAlpha": (
            float(protocol["familywiseAlpha"]) / expected_hypotheses
        ),
    }:
        raise ValidationError("Analysis multiplicity settings do not match protocol")
    if not isinstance(analysis["families"], list) \
            or len(analysis["families"]) != len(protocol["families"]):
        raise ValidationError("Analysis does not cover every family")
    expected_family_ids = [family["id"] for family in protocol["families"]]
    if [family.get("familyId") for family in analysis["families"]] != expected_family_ids:
        raise ValidationError("Analysis family order or identity does not match protocol")
    raw_support_values: list[float] = []
    adjusted_support_values: list[float] = []
    raw_contradiction_values: list[float] = []
    adjusted_contradiction_values: list[float] = []
    for index, family in enumerate(analysis["families"]):
        protocol_family = protocol["families"][index]
        _exact_keys(
            family,
            {
                "familyId", "candidateId", "plannedRuns", "observedRuns", "absoluteQuality",
                "comparisons", "decisionGate", "robustness", "decision", "reasons",
            },
            f"$analysis.families[{index}]",
        )
        if family["decision"] not in ALLOWED_DECISIONS:
            raise ValidationError("Invalid routing decision")
        if family["candidateId"] != protocol_family["candidateId"]:
            raise ValidationError("Analysis candidate does not match protocol")
        expected_runs = (
            len(protocol_family["heldOutFixtureIds"])
            * protocol["replicatesPerFixture"] * len(protocol["matrix"])
        )
        if family["plannedRuns"] != expected_runs:
            raise ValidationError("Analysis planned run count does not match protocol")
        if family["plannedRuns"] != family["observedRuns"]:
            raise ValidationError("Analysis contains an incomplete family")
        if not isinstance(family["reasons"], list) or any(
            not isinstance(reason, str) or not reason for reason in family["reasons"]
        ) or len(family["reasons"]) != len(set(family["reasons"])):
            raise ValidationError("Analysis reasons must be strings")
        if not isinstance(family["comparisons"], list) \
                or len(family["comparisons"]) != len(protocol["matrix"]) - 1:
            raise ValidationError("Analysis family has the wrong comparison count")
        absolute = _exact_keys(
            family["absoluteQuality"],
            {
                "estimate", "lower95", "upper95", "nominalLower95",
                "nominalUpper95", "floor", "rawSupportP",
                "holmAdjustedSupportP", "rawContradictionP",
                "holmAdjustedContradictionP",
            },
            f"$analysis.families[{index}].absoluteQuality",
        )
        for field in (
            "estimate", "lower95", "upper95", "nominalLower95",
            "nominalUpper95", "floor", "rawSupportP",
            "holmAdjustedSupportP", "rawContradictionP",
            "holmAdjustedContradictionP",
        ):
            _probability(absolute[field], f"$analysis.families[{index}].absoluteQuality.{field}", inclusive=True)
        if absolute["floor"] != protocol_family["absoluteQualityFloor"] \
                or not absolute["lower95"] <= absolute["nominalLower95"] \
                <= absolute["estimate"] <= absolute["nominalUpper95"] \
                <= absolute["upper95"]:
            raise ValidationError("Analysis absolute quality is inconsistent")
        raw_support_values.append(absolute["rawSupportP"])
        adjusted_support_values.append(absolute["holmAdjustedSupportP"])
        raw_contradiction_values.append(absolute["rawContradictionP"])
        adjusted_contradiction_values.append(absolute["holmAdjustedContradictionP"])

        expected_comparators = {
            treatment["id"] for treatment in protocol["matrix"]
            if treatment["id"] != protocol_family["candidateId"]
        }
        observed_comparators: set[str] = set()
        comparison_keys = {
            "comparatorId", "candidatePassRate", "comparatorPassRate", "difference",
            "lower95", "upper95", "nominalLower95", "nominalUpper95",
            "nonInferiorityMargin", "rawNonInferiorityP",
            "holmAdjustedNonInferiorityP", "rawContradictionP",
            "holmAdjustedContradictionP",
        }
        for comparison_index, comparison in enumerate(family["comparisons"]):
            comparison = _exact_keys(
                comparison, comparison_keys,
                f"$analysis.families[{index}].comparisons[{comparison_index}]",
            )
            observed_comparators.add(comparison["comparatorId"])
            for field in (
                "candidatePassRate", "comparatorPassRate", "nonInferiorityMargin",
                "rawNonInferiorityP", "holmAdjustedNonInferiorityP", "rawContradictionP",
                "holmAdjustedContradictionP",
            ):
                _probability(comparison[field], f"comparison.{field}", inclusive=True)
            for field in (
                "difference", "lower95", "upper95", "nominalLower95",
                "nominalUpper95",
            ):
                value = comparison[field]
                if not isinstance(value, (int, float)) or isinstance(value, bool) \
                        or not math.isfinite(float(value)) or not -1 <= value <= 1:
                    raise ValidationError(f"Invalid comparison {field}")
            if comparison["nonInferiorityMargin"] != protocol_family["nonInferiorityMargin"] \
                    or comparison["candidatePassRate"] != absolute["estimate"] \
                    or not comparison["lower95"] <= comparison["nominalLower95"] \
                    <= comparison["difference"] <= comparison["nominalUpper95"] \
                    <= comparison["upper95"]:
                raise ValidationError("Analysis comparison is inconsistent")
            raw_support_values.append(comparison["rawNonInferiorityP"])
            adjusted_support_values.append(comparison["holmAdjustedNonInferiorityP"])
            raw_contradiction_values.append(comparison["rawContradictionP"])
            adjusted_contradiction_values.append(comparison["holmAdjustedContradictionP"])
        if observed_comparators != expected_comparators:
            raise ValidationError("Analysis comparators do not match protocol")

        decision_gate = _exact_keys(
            family["decisionGate"],
            {
                "type", "metric", "comparatorId", "candidateValue", "comparatorValue", "gain",
                "lower95", "upper95", "nominalLower95", "nominalUpper95",
                "minimumGain", "rawSupportP", "holmAdjustedSupportP",
                "rawContradictionP", "holmAdjustedContradictionP",
            },
            f"$analysis.families[{index}].decisionGate",
        )
        expected_metric = (
            "generation-duration-gain-fraction"
            if protocol_family["recommendationType"] == "economy"
            else "verified-completion-rate-difference"
        )
        if decision_gate["type"] != protocol_family["recommendationType"] \
                or decision_gate["metric"] != expected_metric \
                or decision_gate["comparatorId"] != protocol_family["decisionComparatorId"] \
                or decision_gate["minimumGain"] != protocol_family["minimumDecisionGain"]:
            raise ValidationError("Analysis decision gate does not match protocol")
        for field in (
            "candidateValue", "comparatorValue", "gain", "lower95", "upper95",
            "minimumGain", "nominalLower95", "nominalUpper95",
        ):
            value = decision_gate[field]
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or not math.isfinite(float(value)):
                raise ValidationError(f"Invalid decision gate {field}")
        if decision_gate["candidateValue"] < 0 or decision_gate["comparatorValue"] < 0 \
                or not decision_gate["lower95"] <= decision_gate["nominalLower95"] \
                <= decision_gate["gain"] <= decision_gate["nominalUpper95"] \
                <= decision_gate["upper95"]:
            raise ValidationError("Analysis decision interval is inconsistent")
        for field in (
            "rawSupportP", "holmAdjustedSupportP", "rawContradictionP",
            "holmAdjustedContradictionP",
        ):
            _probability(decision_gate[field], f"decisionGate.{field}", inclusive=True)
        raw_support_values.append(decision_gate["rawSupportP"])
        adjusted_support_values.append(decision_gate["holmAdjustedSupportP"])
        raw_contradiction_values.append(decision_gate["rawContradictionP"])
        adjusted_contradiction_values.append(decision_gate["holmAdjustedContradictionP"])

        robustness = _exact_keys(
            family["robustness"],
            {"thresholds", "machines", "ecosystems", "leaveOneFixtureOut", "fixtures",
             "heterogeneity", "passed", "reasons"},
            f"$analysis.families[{index}].robustness",
        )
        expected_thresholds = {
            **protocol["robustness"],
            "qualityBoundary": protocol_family["absoluteQualityFloor"]
            - protocol["robustness"]["maximumQualityFloorShortfall"],
            "decisionGainBoundary": protocol_family["minimumDecisionGain"]
            - protocol["robustness"]["maximumDecisionGainShortfall"],
        }
        if robustness["thresholds"] != expected_thresholds:
            raise ValidationError("Analysis robustness thresholds do not match protocol")
        expected_counts = {
            "machines": len(protocol["machines"]),
            "ecosystems": len(set(protocol_family["heldOutFixtureEcosystems"])),
            "leaveOneFixtureOut": len(protocol_family["heldOutFixtureIds"]),
            "fixtures": len(protocol_family["heldOutFixtureIds"]),
        }
        for field, count in expected_counts.items():
            if not isinstance(robustness[field], list) or len(robustness[field]) != count:
                raise ValidationError(f"Analysis robustness {field} coverage is incomplete")
        heterogeneity = _exact_keys(
            robustness["heterogeneity"],
            {"fixtureCandidateRateRange", "fixtureDecisionGainRange"},
            f"$analysis.families[{index}].robustness.heterogeneity",
        )
        if any(not isinstance(value, (int, float)) or isinstance(value, bool)
               or not math.isfinite(float(value)) or value < 0
               for value in heterogeneity.values()):
            raise ValidationError("Analysis robustness heterogeneity is invalid")
        expected_robustness_reasons = _robustness_reasons(robustness)
        if not isinstance(robustness["passed"], bool) \
                or robustness["reasons"] != expected_robustness_reasons \
                or robustness["passed"] != (not expected_robustness_reasons):
            raise ValidationError("Analysis robustness decision is inconsistent")

    if holm_adjust(raw_support_values) != adjusted_support_values \
            or holm_adjust(raw_contradiction_values) != adjusted_contradiction_values:
        raise ValidationError("Analysis Holm adjustments are inconsistent")
    alpha = float(protocol["familywiseAlpha"])
    for family in analysis["families"]:
        expected_decision, expected_reasons = _classify_family_analysis(family, alpha)
        if family["decision"] != expected_decision or family["reasons"] != expected_reasons:
            raise ValidationError("Analysis decision or reasons are inconsistent")
    return analysis


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    validate_parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    validate_parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    plan_parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    plan_parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    plan_parser.add_argument(
        "--runtime-manifest", type=Path,
        default=ROOT / "protocols" / "routing-runtime-v1.json",
    )
    plan_parser.add_argument(
        "--preflight", type=Path, action="append", required=True,
        help="Repeat once for every protocol machine.",
    )
    plan_parser.add_argument("--id-key-file", type=Path, required=True)
    plan_parser.add_argument(
        "--construct-readiness", type=Path, required=True,
        help="Hash-bound construct-validity report; every family must be eligible.",
    )
    plan_parser.add_argument("--output", type=Path, required=True)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("results", nargs="+", type=Path)
    analyze_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    analyze_parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    analyze_parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    analyze_parser.add_argument("--plan", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify-analysis")
    verify_parser.add_argument("results", nargs="+", type=Path)
    verify_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    verify_parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    verify_parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    verify_parser.add_argument("--plan", type=Path, required=True)
    verify_parser.add_argument("--analysis", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    protocol = validate_protocol(load_json(args.protocol))
    validate_protocol_sources(protocol, load_json(args.matrix), load_json(args.catalog))
    if args.command == "validate":
        print(json.dumps({"valid": True, "protocolHash": value_hash(protocol)}, indent=2))
        return 0
    if args.command == "plan":
        import construct_readiness

        construct_readiness.assert_campaign_ready(
            load_json(args.construct_readiness), protocol, load_json(args.catalog)
        )
        key = args.id_key_file.read_bytes()
        runtime_manifest = load_json(args.runtime_manifest)
        reports = [load_json(path) for path in args.preflight]
        plan = make_plan(protocol, key, reports, runtime_manifest)
        save_json(args.output, plan)
        print(json.dumps({"jobs": len(plan["jobs"]), "planHash": value_hash(plan)}, indent=2))
        return 0
    plan = load_json(args.plan)
    validate_plan(plan, protocol)
    results = [load_json(path) for path in args.results]
    computed = analyze(protocol, plan, results)
    if args.command == "analyze":
        save_json(args.output, computed)
        print(json.dumps({
            "analysisHash": value_hash(computed),
            "decisions": {item["familyId"]: item["decision"] for item in computed["families"]},
        }, indent=2))
        return 0
    observed = load_json(args.analysis)
    validate_analysis(observed, protocol, plan)
    if canonical_json(observed) != canonical_json(computed):
        raise ValidationError("Analysis does not reproduce from the supplied cohort")
    print(json.dumps({"valid": True, "analysisHash": value_hash(observed)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
