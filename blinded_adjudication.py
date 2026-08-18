#!/usr/bin/env python3
"""Prepare and verify blinded human adjudication for rubric routing fixtures."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import routing_campaign
import routing_tasks


ROOT = Path(__file__).resolve().parent
RUBRIC_ADAPTER = "artifact-rubric-v1"


class AdjudicationError(ValueError):
    """Raised when a blinded packet or rating cohort is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600 if private else 0o644)


def _files(root: Path) -> list[dict[str, str]]:
    if not root.is_dir():
        raise AdjudicationError(f"missing adjudication source directory: {root}")
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AdjudicationError(f"symlink is forbidden in adjudication input: {path}")
        if path.is_file():
            result.append({
                "path": path.relative_to(root).as_posix(),
                "text": path.read_text(encoding="utf-8"),
            })
    if not result:
        raise AdjudicationError(f"empty adjudication source directory: {root}")
    return result


def _case_payload(task: dict[str, Any], source_type: str) -> dict[str, Any]:
    root = routing_tasks.template_root(task)
    candidate_root = root / ("reference" if source_type == "reference" else "mutants/negative")
    payload = {
        "catalogFamilyId": task["family"],
        "taskId": task["id"],
        "starterFiles": _files(root / "starter"),
        "candidateFiles": _files(candidate_root),
    }
    payload["contentHash"] = value_hash(payload)
    return payload


def prepare(
    protocol: dict[str, Any], catalog: dict[str, Any], key: bytes
) -> tuple[dict[str, Any], dict[str, Any]]:
    routing_campaign.validate_protocol_sources(protocol, {"configurations": protocol["matrix"]}, catalog)
    if len(key) < 16:
        raise AdjudicationError("blinding key must contain at least 16 bytes")
    by_id = {task["id"]: task for task in catalog["tasks"]}
    raw_cases = []
    for family in protocol["families"]:
        for task_id in family["heldOutFixtureIds"]:
            task = by_id[task_id]
            if task["adapter"] != RUBRIC_ADAPTER:
                continue
            for source_type, automated_accepted in (("reference", True), ("negative-mutant", False)):
                payload = _case_payload(task, source_type)
                opaque = hmac.new(
                    key, f"{task_id}:{source_type}".encode("utf-8"), hashlib.sha256
                ).hexdigest()[:24]
                raw_cases.append((opaque, payload, source_type, automated_accepted))
    raw_cases.sort(key=lambda item: hmac.new(key, item[0].encode(), hashlib.sha256).digest())
    protocol_hash, catalog_hash = value_hash(protocol), value_hash(catalog)
    assignment = {
        "schemaVersion": 1,
        "recordKind": "blinded-adjudication-assignment",
        "blinded": True,
        "protocolHash": protocol_hash,
        "catalogHash": catalog_hash,
        "cases": [{"caseId": opaque, **payload} for opaque, payload, _, _ in raw_cases],
    }
    assignment_hash = value_hash(assignment)
    reveal = {
        "schemaVersion": 1,
        "recordKind": "blinded-adjudication-reveal",
        "assignmentHash": assignment_hash,
        "cases": [
            {
                "caseId": opaque,
                "taskId": payload["taskId"],
                "sourceType": source_type,
                "automatedAccepted": automated_accepted,
            }
            for opaque, payload, source_type, automated_accepted in raw_cases
        ],
    }
    return assignment, reveal


def rating_template(assignment: dict[str, Any], rater_id: str) -> dict[str, Any]:
    if not isinstance(rater_id, str) or not rater_id.strip():
        raise AdjudicationError("rater ID must be non-empty")
    validate_assignment(assignment)
    return {
        "schemaVersion": 1,
        "recordKind": "blinded-adjudication-rating",
        "assignmentHash": value_hash(assignment),
        "raterId": rater_id.strip(),
        "ratings": [{"caseId": case["caseId"], "accepted": None} for case in assignment["cases"]],
    }


def review_assignment(
    assignment: dict[str, Any], rater_id: str, *, input_fn=input, output_fn=print
) -> dict[str, Any]:
    """Collect one complete independent rating without exposing the reveal."""
    record = rating_template(assignment, rater_id)
    total = len(assignment["cases"])
    for index, case in enumerate(assignment["cases"], 1):
        output_fn(f"\n=== Case {index}/{total}: {case['caseId']} ===")
        output_fn(f"Family: {case['catalogFamilyId']}\nTask: {case['taskId']}")
        for heading, files in (("STARTER", case["starterFiles"]), ("CANDIDATE", case["candidateFiles"])):
            output_fn(f"\n--- {heading} ---")
            for file in files:
                output_fn(f"\n[{file['path']}]\n{file['text']}")
        while True:
            answer = input_fn("Does the candidate fully satisfy the task? [y/n]: ").strip().lower()
            if answer in {"y", "yes", "n", "no"}:
                record["ratings"][index - 1]["accepted"] = answer in {"y", "yes"}
                break
            output_fn("Please answer y or n.")
    return record


def validate_assignment(assignment: Any) -> dict[str, Any]:
    required = {"schemaVersion", "recordKind", "blinded", "protocolHash", "catalogHash", "cases"}
    if not isinstance(assignment, dict) or set(assignment) != required \
            or assignment.get("schemaVersion") != 1 \
            or assignment.get("recordKind") != "blinded-adjudication-assignment" \
            or assignment.get("blinded") is not True:
        raise AdjudicationError("invalid blinded assignment")
    cases = assignment.get("cases")
    if not isinstance(cases, list) or not cases:
        raise AdjudicationError("blinded assignment has no cases")
    ids = []
    for case in cases:
        expected = {"caseId", "catalogFamilyId", "taskId", "starterFiles", "candidateFiles", "contentHash"}
        if not isinstance(case, dict) or set(case) != expected:
            raise AdjudicationError("invalid blinded assignment case")
        unsigned = {
            key: case[key] for key in (
                "catalogFamilyId", "taskId", "starterFiles", "candidateFiles"
            )
        }
        if case["contentHash"] != value_hash(unsigned):
            raise AdjudicationError("assignment content hash mismatch")
        ids.append(case["caseId"])
    if len(ids) != len(set(ids)):
        raise AdjudicationError("duplicate blinded assignment case ID")
    return assignment


def aggregate(
    protocol: dict[str, Any], catalog: dict[str, Any], assignment: dict[str, Any],
    reveal: dict[str, Any], ratings: list[dict[str, Any]],
) -> dict[str, Any]:
    validate_assignment(assignment)
    expected_assignment, _ = prepare_from_reveal_sources(protocol, catalog, assignment, reveal)
    if expected_assignment != assignment:
        raise AdjudicationError("assignment content differs from frozen fixture sources")
    assignment_hash = value_hash(assignment)
    if not isinstance(reveal, dict) or set(reveal) != {
        "schemaVersion", "recordKind", "assignmentHash", "cases"
    } or reveal.get("schemaVersion") != 1 \
            or reveal.get("recordKind") != "blinded-adjudication-reveal" \
            or reveal.get("assignmentHash") != assignment_hash:
        raise AdjudicationError("reveal is not bound to assignment")
    reveal_cases = reveal.get("cases")
    if not isinstance(reveal_cases, list) or any(
        not isinstance(item, dict) or set(item) != {
            "caseId", "taskId", "sourceType", "automatedAccepted"
        } for item in reveal_cases
    ):
        raise AdjudicationError("reveal case shape is invalid")
    reveal_by_id = {item["caseId"]: item for item in reveal_cases}
    case_ids = [case["caseId"] for case in assignment["cases"]]
    if len(reveal_by_id) != len(reveal_cases) or set(reveal_by_id) != set(case_ids):
        raise AdjudicationError("reveal case set differs from assignment")
    if any(
        item["automatedAccepted"] != (item["sourceType"] == "reference")
        for item in reveal_cases
    ):
        raise AdjudicationError("reveal outcome contradicts frozen source type")
    if len(ratings) < 2:
        raise AdjudicationError("at least two independent ratings are required")
    rater_ids, normalized = [], []
    for rating in ratings:
        if not isinstance(rating, dict) or set(rating) != {
            "schemaVersion", "recordKind", "assignmentHash", "raterId", "ratings"
        } or rating.get("schemaVersion") != 1 \
                or rating.get("recordKind") != "blinded-adjudication-rating" \
                or rating.get("assignmentHash") != assignment_hash:
            raise AdjudicationError("invalid or unbound rating record")
        rater_id = rating.get("raterId")
        if not isinstance(rater_id, str) or not rater_id.strip():
            raise AdjudicationError("rating record has invalid rater ID")
        values = rating.get("ratings")
        if not isinstance(values, list) or len(values) != len(case_ids):
            raise AdjudicationError("rating record is incomplete")
        if any(not isinstance(item, dict) or set(item) != {"caseId", "accepted"} for item in values):
            raise AdjudicationError("rating record case shape is invalid")
        by_id = {item["caseId"]: item["accepted"] for item in values}
        if set(by_id) != set(case_ids) or any(type(value) is not bool for value in by_id.values()):
            raise AdjudicationError("rating record must contain one Boolean decision per case")
        rater_ids.append(rater_id.strip())
        normalized.append({"raterId": rater_id.strip(), "ratings": by_id})
    if len(rater_ids) != len(set(rater_ids)):
        raise AdjudicationError("rater IDs must be distinct")
    audit_cases, grouped = [], {}
    for case in assignment["cases"]:
        case_id = case["caseId"]
        decisions = [record["ratings"][case_id] for record in normalized]
        human_agreement = len(set(decisions)) == 1
        automated = reveal_by_id[case_id].get("automatedAccepted")
        if type(automated) is not bool:
            raise AdjudicationError("reveal automated outcome must be Boolean")
        consensus = decisions[0] if human_agreement else None
        resolved = human_agreement and consensus == automated
        audit_cases.append({
            "caseId": case_id,
            "catalogFamilyId": case["catalogFamilyId"],
            "taskId": case["taskId"],
            "sourceType": reveal_by_id[case_id].get("sourceType"),
            "automatedAccepted": automated,
            "humanRatings": decisions,
            "consensusAccepted": consensus,
            "resolved": resolved,
        })
        grouped.setdefault(case["catalogFamilyId"], []).append((human_agreement, resolved))
    families = []
    for family_id, outcomes in sorted(grouped.items()):
        families.append({
            "catalogFamilyId": family_id,
            "sampleSize": len(outcomes),
            "agreement": sum(item[0] for item in outcomes) / len(outcomes),
            "unresolvedDisagreements": sum(not item[1] for item in outcomes),
        })
    rating_records = sorted(ratings, key=lambda item: item["raterId"])
    return {
        "schemaVersion": 2,
        "recordKind": "blinded-adjudication",
        "blinded": True,
        "protocolHash": value_hash(protocol),
        "catalogHash": value_hash(catalog),
        "raterCount": len(ratings),
        "assignmentHash": assignment_hash,
        "revealHash": value_hash(reveal),
        "ratingsHash": value_hash(rating_records),
        "assignment": assignment,
        "reveal": reveal,
        "ratingRecords": rating_records,
        "auditCases": audit_cases,
        "families": families,
    }


def prepare_from_reveal_sources(
    protocol: dict[str, Any], catalog: dict[str, Any], assignment: dict[str, Any], reveal: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild packet contents while preserving the original opaque IDs/order."""
    routing_campaign.validate_protocol_sources(protocol, {"configurations": protocol["matrix"]}, catalog)
    by_task = {task["id"]: task for task in catalog["tasks"]}
    reveal_by_id = {item["caseId"]: item for item in reveal.get("cases", []) if isinstance(item, dict)}
    rebuilt = []
    for case in assignment.get("cases", []):
        item = reveal_by_id.get(case.get("caseId"))
        if not item or item.get("taskId") != case.get("taskId"):
            raise AdjudicationError("reveal task mapping is invalid")
        source_type = item.get("sourceType")
        if source_type not in {"reference", "negative-mutant"}:
            raise AdjudicationError("reveal source type is invalid")
        rebuilt.append({"caseId": case["caseId"], **_case_payload(by_task[case["taskId"]], source_type)})
    expected = {
        "schemaVersion": 1, "recordKind": "blinded-adjudication-assignment", "blinded": True,
        "protocolHash": value_hash(protocol), "catalogHash": value_hash(catalog), "cases": rebuilt,
    }
    return expected, reveal


def validate_artifact(
    artifact: Any, protocol: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(artifact, dict) or artifact.get("schemaVersion") != 2:
        raise AdjudicationError("unsupported blinded adjudication artifact")
    rebuilt = aggregate(
        protocol, catalog, artifact.get("assignment"), artifact.get("reveal"),
        artifact.get("ratingRecords") if isinstance(artifact.get("ratingRecords"), list) else [],
    )
    if rebuilt != artifact:
        raise AdjudicationError("blinded adjudication artifact does not reproduce")
    return artifact


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--protocol", type=Path, default=routing_campaign.DEFAULT_PROTOCOL)
    prepare_parser.add_argument("--catalog", type=Path, default=ROOT / "fixtures/catalog.json")
    prepare_parser.add_argument("--key-file", type=Path, required=True)
    prepare_parser.add_argument("--assignment", type=Path, required=True)
    prepare_parser.add_argument("--reveal", type=Path, required=True)
    rate_parser = sub.add_parser("rating-template")
    rate_parser.add_argument("--assignment", type=Path, required=True)
    rate_parser.add_argument("--rater-id", required=True)
    rate_parser.add_argument("--output", type=Path, required=True)
    review_parser = sub.add_parser("review")
    review_parser.add_argument("--assignment", type=Path, required=True)
    review_parser.add_argument("--rater-id", required=True)
    review_parser.add_argument("--output", type=Path, required=True)
    aggregate_parser = sub.add_parser("aggregate")
    aggregate_parser.add_argument("--protocol", type=Path, default=routing_campaign.DEFAULT_PROTOCOL)
    aggregate_parser.add_argument("--catalog", type=Path, default=ROOT / "fixtures/catalog.json")
    aggregate_parser.add_argument("--assignment", type=Path, required=True)
    aggregate_parser.add_argument("--reveal", type=Path, required=True)
    aggregate_parser.add_argument("--rating", type=Path, action="append", required=True)
    aggregate_parser.add_argument("--output", type=Path, required=True)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("artifact", type=Path)
    check_parser.add_argument("--protocol", type=Path, default=routing_campaign.DEFAULT_PROTOCOL)
    check_parser.add_argument("--catalog", type=Path, default=ROOT / "fixtures/catalog.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "prepare":
        assignment, reveal = prepare(load_json(args.protocol), load_json(args.catalog), args.key_file.read_bytes())
        save_json(args.assignment, assignment)
        save_json(args.reveal, reveal, private=True)
        print(json.dumps({"assignmentHash": value_hash(assignment), "cases": len(assignment["cases"])}))
    elif args.command == "rating-template":
        template = rating_template(load_json(args.assignment), args.rater_id)
        save_json(args.output, template, private=True)
    elif args.command == "review":
        record = review_assignment(load_json(args.assignment), args.rater_id)
        save_json(args.output, record, private=True)
        print(json.dumps({"complete": True, "ratings": len(record["ratings"])}))
    elif args.command == "aggregate":
        artifact = aggregate(
            load_json(args.protocol), load_json(args.catalog), load_json(args.assignment),
            load_json(args.reveal), [load_json(path) for path in args.rating],
        )
        save_json(args.output, artifact)
        print(json.dumps({"families": artifact["families"], "ratingsHash": artifact["ratingsHash"]}))
    else:
        validate_artifact(load_json(args.artifact), load_json(args.protocol), load_json(args.catalog))
        print(json.dumps({"valid": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
