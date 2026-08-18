#!/usr/bin/env python3
"""Deterministic, resumable cheapest-sufficient routing execution.

The ordinary routing plan remains the immutable maximum envelope.  This module
only selects a prefix of its already assigned jobs; it never creates new run
IDs or changes a treatment assignment.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

import routing_campaign


SCHEMA_VERSION = 1
MANIFEST_KIND = "routing-sequential-manifest"
STATE_KIND = "routing-sequential-state"
ANALYSIS_KIND = "routing-sequential-analysis"
TERMINAL_DECISIONS = {"ACCEPT", "EXHAUSTED"}
ACTIVE_DECISION = "CONTINUE"


class SequentialError(ValueError):
    """Raised when a sequential manifest, state, or transition is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _exact(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise SequentialError(f"{path} must contain exactly {sorted(keys)}; got {actual}")
    return value


def _hash(value: Any, path: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise SequentialError(f"{path} must be a canonical SHA-256")


def _unsigned(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _family_by_id(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {family["id"]: family for family in protocol["families"]}


def _stages_by_family(manifest: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {family["familyId"]: family["stages"] for family in manifest["families"]}


def make_manifest(plan: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    """Derive one deterministic six-stage ladder from the frozen matrix order."""
    routing_campaign.validate_plan(plan, protocol)
    matrix_order = [item["id"] for item in protocol["matrix"]]
    families = []
    for family in protocol["families"]:
        stages = []
        for index, treatment_id in enumerate(matrix_order):
            run_ids = [
                job["runId"] for job in plan["jobs"]
                if job["familyId"] == family["id"] and job["treatmentId"] == treatment_id
            ]
            if not run_ids:
                raise SequentialError(f"No planned runs for {family['id']}/{treatment_id}")
            stages.append({
                "stageIndex": index,
                "stageId": f"{family['id']}:stage-{index}",
                "familyId": family["id"],
                "treatmentId": treatment_id,
                "runIds": run_ids,
            })
        families.append({"familyId": family["id"], "stages": stages})
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "recordKind": MANIFEST_KIND,
        "protocolHash": routing_campaign.value_hash(protocol),
        "planHash": routing_campaign.value_hash(plan),
        "matrixOrder": matrix_order,
        "families": families,
        "potentialJobs": len(plan["jobs"]),
    }
    manifest["manifestHash"] = value_hash(manifest)
    validate_manifest(manifest, plan, protocol)
    return manifest


def validate_manifest(manifest: Any, plan: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    routing_campaign.validate_plan(plan, protocol)
    manifest = _exact(
        manifest,
        {"schemaVersion", "recordKind", "protocolHash", "planHash", "matrixOrder", "families", "potentialJobs", "manifestHash"},
        "$manifest",
    )
    if manifest["schemaVersion"] != SCHEMA_VERSION or manifest["recordKind"] != MANIFEST_KIND:
        raise SequentialError("unsupported sequential manifest identity")
    if manifest["protocolHash"] != routing_campaign.value_hash(protocol):
        raise SequentialError("sequential manifest protocol hash mismatch")
    if manifest["planHash"] != routing_campaign.value_hash(plan):
        raise SequentialError("sequential manifest plan hash mismatch")
    if manifest["manifestHash"] != value_hash(_unsigned(manifest, "manifestHash")):
        raise SequentialError("sequential manifest hash mismatch")
    matrix_order = [item["id"] for item in protocol["matrix"]]
    if manifest["matrixOrder"] != matrix_order:
        raise SequentialError("sequential matrix order differs from protocol")
    if manifest["potentialJobs"] != len(plan["jobs"]):
        raise SequentialError("sequential potential job count mismatch")
    family_ids = [family["id"] for family in protocol["families"]]
    if not isinstance(manifest["families"], list) or [item.get("familyId") for item in manifest["families"]] != family_ids:
        raise SequentialError("sequential manifest family order mismatch")
    expected_by_id = {job["runId"]: job for job in plan["jobs"]}
    seen: set[str] = set()
    for family_index, family_record in enumerate(manifest["families"]):
        family_record = _exact(family_record, {"familyId", "stages"}, f"$manifest.families[{family_index}]")
        family_id = family_ids[family_index]
        stages = family_record["stages"]
        if not isinstance(stages, list) or len(stages) != len(matrix_order):
            raise SequentialError(f"{family_id} must have one stage per treatment")
        for stage_index, stage in enumerate(stages):
            stage = _exact(stage, {"stageIndex", "stageId", "familyId", "treatmentId", "runIds"}, f"$manifest.{family_id}.stages[{stage_index}]")
            treatment_id = matrix_order[stage_index]
            if stage["stageIndex"] != stage_index or stage["familyId"] != family_id \
                    or stage["treatmentId"] != treatment_id \
                    or stage["stageId"] != f"{family_id}:stage-{stage_index}":
                raise SequentialError(f"invalid stage identity for {family_id}/{treatment_id}")
            if not isinstance(stage["runIds"], list) or not stage["runIds"]:
                raise SequentialError(f"stage {stage['stageId']} has no run IDs")
            for run_id in stage["runIds"]:
                if run_id in seen or run_id not in expected_by_id:
                    raise SequentialError(f"manifest contains duplicate or unknown run {run_id}")
                job = expected_by_id[run_id]
                if job["familyId"] != family_id or job["treatmentId"] != treatment_id:
                    raise SequentialError(f"run {run_id} does not belong to its stage")
                seen.add(run_id)
    if seen != set(expected_by_id):
        raise SequentialError("manifest does not cover the complete immutable plan")
    return manifest


def _stage_record(manifest: dict[str, Any], family_id: str, index: int) -> dict[str, Any]:
    stages = _stages_by_family(manifest).get(family_id)
    if stages is None or index < 0 or index >= len(stages):
        raise SequentialError(f"unknown sequential stage {family_id}/{index}")
    return stages[index]


def _authorized_record(manifest: dict[str, Any], family_id: str, index: int) -> dict[str, Any]:
    stage = _stage_record(manifest, family_id, index)
    return {
        "familyId": family_id,
        "stageIndex": index,
        "stageId": stage["stageId"],
        "treatmentId": stage["treatmentId"],
        "runIds": list(stage["runIds"]),
    }


def make_initial_state(manifest: dict[str, Any], plan: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest, plan, protocol)
    authorized = [_authorized_record(manifest, family["id"], 0) for family in protocol["families"]]
    state = {
        "schemaVersion": SCHEMA_VERSION,
        "recordKind": STATE_KIND,
        "protocolHash": routing_campaign.value_hash(protocol),
        "planHash": routing_campaign.value_hash(plan),
        "manifestHash": manifest["manifestHash"],
        "sequence": 0,
        "previousStateHash": None,
        "authorized": authorized,
        "decisions": [
            {"familyId": family["id"], "decision": "PENDING", "selectedTreatmentId": None}
            for family in protocol["families"]
        ],
        "history": [],
        "complete": False,
    }
    state["stateHash"] = value_hash(state)
    validate_state(state, manifest, plan, protocol)
    return state


def _validate_event(event: Any, index: int, previous_hash: str | None) -> None:
    event = _exact(event, {"sequence", "previousEventHash", "resultHashes", "metrics", "decisions", "eventHash"}, f"$state.history[{index}]")
    if event["sequence"] != index + 1 or event["previousEventHash"] != previous_hash:
        raise SequentialError("sequential state history chain is broken")
    if event["eventHash"] != value_hash(_unsigned(event, "eventHash")):
        raise SequentialError("sequential event hash mismatch")
    if not isinstance(event["resultHashes"], list) or not isinstance(event["metrics"], list) \
            or not isinstance(event["decisions"], list):
        raise SequentialError("sequential event collections are invalid")
    for item in event["resultHashes"]:
        item = _exact(item, {"runId", "resultHash", "status"}, "$.resultHashes")
        _hash(item["resultHash"], "$.resultHashes.resultHash")
        if item["status"] not in {"PASS", "CANDIDATE_FAILURE"}:
            raise SequentialError("sequential history contains unresolved status")


def validate_state(state: Any, manifest: dict[str, Any], plan: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest, plan, protocol)
    state = _exact(
        state,
        {"schemaVersion", "recordKind", "protocolHash", "planHash", "manifestHash", "sequence", "previousStateHash", "authorized", "decisions", "history", "complete", "stateHash"},
        "$state",
    )
    if state["schemaVersion"] != SCHEMA_VERSION or state["recordKind"] != STATE_KIND:
        raise SequentialError("unsupported sequential state identity")
    if state["protocolHash"] != routing_campaign.value_hash(protocol) or state["planHash"] != routing_campaign.value_hash(plan) \
            or state["manifestHash"] != manifest["manifestHash"]:
        raise SequentialError("sequential state source hash mismatch")
    if state["stateHash"] != value_hash(_unsigned(state, "stateHash")):
        raise SequentialError("sequential state hash mismatch")
    if not isinstance(state["sequence"], int) or isinstance(state["sequence"], bool) or state["sequence"] < 0:
        raise SequentialError("invalid sequential state sequence")
    if not isinstance(state["history"], list) or len(state["history"]) != state["sequence"]:
        raise SequentialError("sequential state history length mismatch")
    previous_hash = None
    for index, event in enumerate(state["history"]):
        _validate_event(event, index, previous_hash)
        previous_hash = event["eventHash"]
    if state["sequence"] == 0:
        if state["previousStateHash"] is not None:
            raise SequentialError("initial sequential state cannot have a predecessor")
    else:
        _hash(state["previousStateHash"], "$state.previousStateHash")
    family_ids = [family["id"] for family in protocol["families"]]
    if not isinstance(state["decisions"], list) or [item.get("familyId") for item in state["decisions"]] != family_ids:
        raise SequentialError("sequential state decision order mismatch")
    treatment_ids = set(manifest["matrixOrder"])
    for item in state["decisions"]:
        item = _exact(item, {"familyId", "decision", "selectedTreatmentId"}, "$.state.decisions")
        if item["decision"] not in {"PENDING", ACTIVE_DECISION, *TERMINAL_DECISIONS}:
            raise SequentialError("invalid sequential family decision")
        if item["decision"] == "ACCEPT":
            if item["selectedTreatmentId"] not in treatment_ids:
                raise SequentialError("accepted family must select a matrix treatment")
        elif item["selectedTreatmentId"] is not None:
            raise SequentialError("only an accepted family may select a treatment")
        if item["decision"] == "PENDING" and state["sequence"] != 0:
            raise SequentialError("pending decisions are valid only in the initial state")
    if not isinstance(state["authorized"], list):
        raise SequentialError("sequential authorized collection is invalid")
    authorized_families = set()
    for item in state["authorized"]:
        item = _exact(item, {"familyId", "stageIndex", "stageId", "treatmentId", "runIds"}, "$.state.authorized")
        if item["familyId"] in authorized_families:
            raise SequentialError("duplicate authorized family")
        authorized_families.add(item["familyId"])
        expected = _authorized_record(manifest, item["familyId"], item["stageIndex"])
        if item != expected:
            raise SequentialError(f"authorized stage differs from manifest for {item['familyId']}")
    decision_by_family = {item["familyId"]: item for item in state["decisions"]}
    nonterminal_families = {
        item["familyId"] for item in state["decisions"]
        if item["decision"] in {"PENDING", ACTIVE_DECISION}
    }
    if authorized_families != nonterminal_families:
        raise SequentialError("authorized stages must exactly match nonterminal families")
    for item in state["authorized"]:
        if decision_by_family[item["familyId"]]["decision"] == "PENDING" \
                and item["stageIndex"] != 0:
            raise SequentialError("an initial pending family must authorize stage zero")
    if state["complete"] is not all(item["decision"] in TERMINAL_DECISIONS for item in state["decisions"]):
        raise SequentialError("sequential complete flag is inconsistent")
    return state


def _gate(family: dict[str, Any], jobs: list[dict[str, Any]], results: Mapping[str, dict[str, Any]], protocol: dict[str, Any]) -> dict[str, Any]:
    floor = float(family["absoluteQualityFloor"])
    boundary = floor - float(protocol["robustness"]["maximumQualityFloorShortfall"])
    passed = [result["status"] == "PASS" for result in (results[job["runId"]] for job in jobs)]
    overall = sum(passed) / len(passed)
    machine_rates: dict[str, float] = {}
    ecosystem_rates: dict[str, float] = {}
    ecosystems = dict(zip(family["heldOutFixtureIds"], family["heldOutFixtureEcosystems"]))
    for machine in sorted({job["machineId"] for job in jobs}):
        values = [results[job["runId"]]["status"] == "PASS" for job in jobs if job["machineId"] == machine]
        machine_rates[machine] = sum(values) / len(values)
    for ecosystem in sorted(set(family["heldOutFixtureEcosystems"])):
        values = [results[job["runId"]]["status"] == "PASS" for job in jobs if ecosystems[job["fixtureId"]] == ecosystem]
        ecosystem_rates[ecosystem] = sum(values) / len(values)
    accepted = overall >= floor and all(rate >= boundary for rate in machine_rates.values()) \
        and all(rate >= boundary for rate in ecosystem_rates.values())
    return {
        "floor": floor,
        "boundary": boundary,
        "overallPassRate": overall,
        "machinePassRates": machine_rates,
        "ecosystemPassRates": ecosystem_rates,
        "accepted": accepted,
    }


def advance_state(state: dict[str, Any], manifest: dict[str, Any], plan: dict[str, Any], protocol: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    validate_state(state, manifest, plan, protocol)
    by_run = {job["runId"]: job for job in plan["jobs"]}
    result_list = list(results.values())
    if result_list:
        routing_campaign.validate_results(result_list, plan, protocol)
    result_hashes = []
    decisions = []
    metrics = []
    next_authorized = []
    families = _family_by_id(protocol)
    for authorization in state["authorized"]:
        family_id = authorization["familyId"]
        run_ids = authorization["runIds"]
        missing = [run_id for run_id in run_ids if run_id not in results]
        if missing:
            raise SequentialError(f"stage {authorization['stageId']} is incomplete; missing {missing[0]}")
        unresolved = [run_id for run_id in run_ids if results[run_id]["status"] == "INFRA_FAILURE"]
        if unresolved:
            raise SequentialError(f"stage {authorization['stageId']} is paused by infrastructure failure {unresolved[0]}")
        if any(results[run_id]["status"] not in {"PASS", "CANDIDATE_FAILURE"} for run_id in run_ids):
            raise SequentialError(f"stage {authorization['stageId']} contains an invalid unresolved result")
        for run_id in run_ids:
            result_hashes.append({"runId": run_id, "resultHash": value_hash(results[run_id]), "status": results[run_id]["status"]})
        jobs = [by_run[run_id] for run_id in run_ids]
        gate = _gate(families[family_id], jobs, results, protocol)
        metrics.append({"familyId": family_id, "stageIndex": authorization["stageIndex"], **gate})
        if gate["accepted"]:
            decision = "ACCEPT"
            selected = authorization["treatmentId"]
        elif authorization["stageIndex"] + 1 < len(_stages_by_family(manifest)[family_id]):
            decision = ACTIVE_DECISION
            selected = None
            next_authorized.append(_authorized_record(manifest, family_id, authorization["stageIndex"] + 1))
        else:
            decision = "EXHAUSTED"
            selected = None
        decisions.append({"familyId": family_id, "decision": decision, "selectedTreatmentId": selected})
    current_decisions = {item["familyId"]: item for item in state["decisions"]}
    next_decisions = []
    for family in protocol["families"]:
        next_decisions.append(next((item for item in decisions if item["familyId"] == family["id"]), current_decisions[family["id"]]))
    event = {
        "sequence": state["sequence"] + 1,
        "previousEventHash": None if not state["history"] else state["history"][-1]["eventHash"],
        "resultHashes": sorted(result_hashes, key=lambda item: item["runId"]),
        "metrics": sorted(metrics, key=lambda item: item["familyId"]),
        "decisions": next_decisions,
    }
    event["eventHash"] = value_hash(event)
    next_state = {
        "schemaVersion": SCHEMA_VERSION,
        "recordKind": STATE_KIND,
        "protocolHash": state["protocolHash"],
        "planHash": state["planHash"],
        "manifestHash": state["manifestHash"],
        "sequence": event["sequence"],
        "previousStateHash": state["stateHash"],
        "authorized": next_authorized,
        "decisions": next_decisions,
        "history": [*state["history"], event],
        "complete": all(item["decision"] in TERMINAL_DECISIONS for item in next_decisions),
    }
    next_state["stateHash"] = value_hash(next_state)
    validate_state(next_state, manifest, plan, protocol)
    return next_state


def replay_state(
    state: dict[str, Any], manifest: dict[str, Any], plan: dict[str, Any],
    protocol: dict[str, Any], results: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """Recompute every transition and reject a self-consistent fabricated state."""
    validate_state(state, manifest, plan, protocol)
    replayed = make_initial_state(manifest, plan, protocol)
    for expected_event in state["history"]:
        replayed = advance_state(replayed, manifest, plan, protocol, results)
        if replayed["history"][-1] != expected_event:
            raise SequentialError("sequential event does not replay from bound results")
    if replayed != state:
        raise SequentialError("sequential terminal state does not reproduce")
    return state


def analyze_state(
    state: dict[str, Any], manifest: dict[str, Any], plan: dict[str, Any],
    protocol: dict[str, Any], results: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    replay_state(state, manifest, plan, protocol, results)
    if not state["complete"]:
        raise SequentialError("sequential analysis requires a terminal state")
    executed = sorted({item["runId"] for event in state["history"] for item in event["resultHashes"]})
    decisions = {item["familyId"]: item for item in state["decisions"]}
    rows = []
    for family in protocol["families"]:
        decision = decisions[family["id"]]
        rows.append({
            "familyId": family["id"],
            "selectedTreatmentId": decision["selectedTreatmentId"],
            "cheapestSufficientConfigurationId": decision["selectedTreatmentId"],
            "decision": decision["decision"],
        })
    potential = len(plan["jobs"])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordKind": ANALYSIS_KIND,
        "protocolHash": state["protocolHash"],
        "planHash": state["planHash"],
        "manifestHash": state["manifestHash"],
        "stateHash": state["stateHash"],
        "complete": True,
        "potentialJobs": potential,
        "executedJobs": len(executed),
        "savedJobs": potential - len(executed),
        "savingsFraction": (potential - len(executed)) / potential,
        "families": rows,
    }
