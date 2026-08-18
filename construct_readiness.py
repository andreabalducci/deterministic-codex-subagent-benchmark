#!/usr/bin/env python3
"""Fail-closed construct-validity gate for routing campaigns.

This module does not score model outputs.  It establishes whether the fixture
instrument is strong enough to justify each *scoped* routing claim before a
paid confirmatory plan can be created or a policy claim can be promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import routing_campaign
import routing_tasks


ROOT = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = ROOT / "protocols" / "routing-operational-v1.json"
DEFAULT_CATALOG = ROOT / "fixtures" / "catalog.json"
DEFAULT_REPORT = ROOT / "runs" / "construct-readiness-current.json"
MIN_CONFIRMATORY_FIXTURES = 6
MIN_ECOSYSTEMS = 3
MIN_SURFACES = 6
MAX_PROMPT_TRIGRAM_JACCARD = 0.85
MIN_CRITERION_MUTATION_COVERAGE = 1.0
MIN_EQUIVALENT_POSITIVES_PER_FIXTURE = 1
ARTIFACT_RUBRIC = "artifact-rubric-v1"

SCOPED_CLAIMS = {
    "mechanical": "exact semantic-JSON repository edits with immutable-file checks",
    "bounded-mapping-patch": "small mapping patches accepted by sealed deterministic commands",
    "isolated-implementation": "isolated implementations accepted by sealed behavioral or data-contract tests",
    "read-heavy-analysis": "structured defect localization with exact source evidence in compact seeded repositories",
    "coordination-integration": "multi-file contract, producer, consumer, and acceptance state integration",
    "high-risk-change": "machine-verified compatibility, implementation, rollback, and acceptance state transitions",
}


class ReadinessError(ValueError):
    """Raised when readiness evidence is malformed or promotion is unsafe."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _prompt_shingles(task: dict[str, Any]) -> set[tuple[str, str, str]]:
    prompt = (
        routing_tasks.template_root(task) / "starter" / "TASK.md"
    ).read_text(encoding="utf-8").lower()
    tokens = re.findall(r"[a-z0-9_.:/-]+", prompt)
    return set(zip(tokens, tokens[1:], tokens[2:]))


def _maximum_prompt_similarity(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    shingled = [(task["id"], _prompt_shingles(task)) for task in tasks]
    for (left_id, left), (right_id, right) in itertools.combinations(shingled, 2):
        union = left | right
        similarity = len(left & right) / len(union) if union else 1.0
        pairs.append((similarity, left_id, right_id))
    similarity, left_id, right_id = max(pairs, default=(1.0, "", ""))
    return {
        "maximum": round(similarity, 6),
        "fixtureIds": [left_id, right_id],
        "threshold": MAX_PROMPT_TRIGRAM_JACCARD,
        "passed": similarity <= MAX_PROMPT_TRIGRAM_JACCARD,
    }


def _case_index(calibration: Any) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if not isinstance(calibration, dict) or not isinstance(calibration.get("cases"), list):
        return {}
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for case in calibration["cases"]:
        if isinstance(case, dict) and isinstance(case.get("template"), str) \
                and isinstance(case.get("case"), str):
            indexed.setdefault((case["template"], case["case"]), []).append(case)
    return indexed


def _valid_docker_calibration(
    calibration: Any, catalog_hash: str, catalog_tasks: list[dict[str, Any]]
) -> tuple[bool, list[str], dict[tuple[str, str], list[dict[str, Any]]]]:
    reasons: list[str] = []
    if not isinstance(calibration, dict):
        return False, ["docker-calibration-artifact-missing"], {}
    required = {
        "schemaVersion", "recordKind", "backend", "catalogHash",
        "evaluatorImage", "evaluatorImageId", "passed", "cases",
    }
    if set(calibration) != required or calibration.get("schemaVersion") != 1 \
            or calibration.get("recordKind") != "routing-calibration" \
            or calibration.get("backend") != "docker":
        return False, ["docker-calibration-artifact-invalid"], {}
    if calibration.get("catalogHash") != catalog_hash:
        reasons.append("docker-calibration-catalog-hash-mismatch")
    image_id = calibration.get("evaluatorImageId")
    if calibration.get("evaluatorImage") != routing_tasks.ROUTING_EVALUATOR_IMAGE \
            or not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        reasons.append("docker-calibration-image-unbound")
    if calibration.get("passed") is not True:
        reasons.append("docker-calibration-not-passed")
    index = _case_index(calibration)
    expected_cases: dict[tuple[str, str, str | None], str] = {}
    for task in catalog_tasks:
        expected_cases[(task["template"], "reference", None)] = "PASS"
        expected_cases[(task["template"], "mutant", None)] = "FAIL"
        expected_cases[(task["template"], "equivalent-positive", None)] = "PASS"
        if task["adapter"] == ARTIFACT_RUBRIC:
            expected_cases[(task["template"], "schema-extra-mutant", None)] = "FAIL"
            root = routing_tasks.template_root(task)
            spec = routing_tasks.load_template(task)
            reference = json.loads(
                (root / "reference" / spec["mutable"][0]).read_text(encoding="utf-8")
            )
            for criterion_id, _ in routing_tasks.artifact_criterion_mutants(
                task, spec, reference
            ):
                expected_cases[(task["template"], "criterion-mutant", criterion_id)] = "FAIL"
        elif task["adapter"] == "json-semantic-diff-v1":
            spec = routing_tasks.load_template(task)
            for criterion_id in routing_tasks.json_state_criterion_ids(spec):
                expected_cases[(task["template"], "criterion-mutant", criterion_id)] = "FAIL"
    observed_keys = [
        (case.get("template"), case.get("case"), case.get("criterionId"))
        for case in calibration["cases"] if isinstance(case, dict)
    ]
    if len(observed_keys) != len(set(observed_keys)) \
            or set(observed_keys) != set(expected_cases):
        reasons.append("docker-calibration-fixture-coverage-incomplete")
    if any(
        not isinstance(case, dict)
        or set(case) != ({"template", "case", "expected", "actual", "criterionId"}
                         if case.get("case") == "criterion-mutant"
                         else {"template", "case", "expected", "actual"})
        or expected_cases.get((case.get("template"), case.get("case"), case.get("criterionId"))) != case.get("expected")
        or case.get("actual") != case.get("expected")
        for case in calibration["cases"]
    ):
        reasons.append("docker-calibration-case-failure-or-shape-invalid")
    return not reasons, reasons, index


@lru_cache(maxsize=None)
def _criterion_coverage_by_id(task_id: str) -> tuple[int, int]:
    """Return critical criteria and those killed by the committed negative mutant."""
    task = _catalog_tasks_by_id()[task_id]
    if task["adapter"] != ARTIFACT_RUBRIC:
        return 1, 1
    root = routing_tasks.template_root(task)
    reference = routing_tasks.evaluate_artifact(
        task["id"], root / "reference", backend="native", trusted_native=True
    )
    mutant = routing_tasks.evaluate_artifact(
        task["id"], root / "mutants" / "negative", backend="native", trusted_native=True
    )
    criteria = {item["id"] for item in reference["outcomes"] if item["critical"]}
    killed = {
        item["id"] for item in mutant["outcomes"]
        if item["critical"] and item["outcome"] == "FAIL"
    }
    return len(criteria), len(criteria & killed)


def _criterion_coverage(
    task: dict[str, Any],
    cases: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> tuple[int, int]:
    if task["adapter"] == "json-semantic-diff-v1":
        criteria = set(routing_tasks.json_state_criterion_ids(
            routing_tasks.load_template(task)
        ))
        if cases is None:
            return len(criteria), 0
        killed = {
            case.get("criterionId")
            for case in cases.get((task["template"], "criterion-mutant"), [])
            if case.get("expected") == "FAIL" and case.get("actual") == "FAIL"
        }
        return len(criteria), len(criteria & killed)
    if task["adapter"] != ARTIFACT_RUBRIC:
        if cases is None:
            return 1, 0
        base_mutant_killed = any(
            case.get("expected") == "FAIL" and case.get("actual") == "FAIL"
            for case in cases.get((task["template"], "mutant"), [])
        )
        return 1, int(base_mutant_killed)
    total, legacy_killed = _criterion_coverage_by_id(task["id"])
    if cases is None:
        return total, legacy_killed
    reference = routing_tasks.evaluate_artifact(
        task["id"], routing_tasks.template_root(task) / "reference",
        backend="native", trusted_native=True,
    )
    criteria = {item["id"] for item in reference["outcomes"] if item["critical"]}
    killed = {
        case.get("criterionId")
        for case in cases.get((task["template"], "criterion-mutant"), [])
        if case.get("expected") == "FAIL" and case.get("actual") == "FAIL"
    }
    return len(criteria), len(criteria & killed)


@lru_cache(maxsize=1)
def _catalog_tasks_by_id() -> dict[str, dict[str, Any]]:
    return {task["id"]: task for task in routing_tasks.load_catalog()["tasks"]}


def build_report(
    protocol: dict[str, Any], catalog: dict[str, Any], *,
    calibration: Any = None,
) -> dict[str, Any]:
    routing_campaign.validate_protocol_sources(protocol, {"configurations": protocol["matrix"]}, catalog)
    protocol_hash, catalog_hash = value_hash(protocol), value_hash(catalog)
    confirmatory = [task for task in catalog["tasks"] if task["kind"] == "confirmatory"]
    docker_ok, docker_reasons, cases = _valid_docker_calibration(
        calibration, catalog_hash, catalog["tasks"]
    )
    results = []
    for family in protocol["families"]:
        family_id = family["catalogFamilyId"]
        selected_ids = set(family["heldOutFixtureIds"])
        tasks = [
            task for task in confirmatory
            if task["family"] == family_id and task["id"] in selected_ids
        ]
        adapters = sorted({task["adapter"] for task in tasks})
        scale = {
            "confirmatoryFixtures": len(tasks),
            "ecosystems": len({task["ecosystem"] for task in tasks}),
            "surfaces": len({task["surface"] for task in tasks}),
            "adapters": adapters,
        }
        scale["passed"] = (
            scale["confirmatoryFixtures"] >= MIN_CONFIRMATORY_FIXTURES
            and scale["ecosystems"] >= MIN_ECOSYSTEMS
            and scale["surfaces"] >= MIN_SURFACES
        )
        similarity = _maximum_prompt_similarity(tasks)
        total_criteria = killed_criteria = 0
        for task in tasks:
            total, killed = _criterion_coverage(task, cases)
            if task["adapter"] == ARTIFACT_RUBRIC:
                # Strict top-level shape is a separate critical construct. The
                # deterministic schema-extra mutant must kill it.
                total += 1
                if any(
                    case.get("expected") == "FAIL" and case.get("actual") == "FAIL"
                    for case in cases.get((task["template"], "schema-extra-mutant"), [])
                ):
                    killed += 1
            total_criteria += total
            killed_criteria += killed
        coverage = killed_criteria / total_criteria if total_criteria else 0.0
        mutation = {
            "criticalCriteria": total_criteria,
            "killedCriticalCriteria": killed_criteria,
            "coverage": round(coverage, 6),
            "threshold": MIN_CRITERION_MUTATION_COVERAGE,
            "passed": coverage >= MIN_CRITERION_MUTATION_COVERAGE,
        }
        equivalent_counts = {
            task["id"]: sum(
                case.get("expected") == "PASS" and case.get("actual") == "PASS"
                for case in cases.get((task["template"], "equivalent-positive"), [])
            ) for task in tasks
        }
        equivalent = {
            "minimumPerFixture": min(equivalent_counts.values(), default=0),
            "requiredPerFixture": MIN_EQUIVALENT_POSITIVES_PER_FIXTURE,
            "coveredFixtures": sum(value >= 1 for value in equivalent_counts.values()),
            "passed": bool(tasks) and all(value >= 1 for value in equivalent_counts.values()),
        }
        reasons = []
        if not scale["passed"]: reasons.append("insufficient-task-scale-or-diversity")
        if not similarity["passed"]: reasons.append("prompt-near-duplication-above-threshold")
        if not mutation["passed"]: reasons.append("critical-criterion-mutation-coverage-incomplete")
        if not equivalent["passed"]: reasons.append("equivalent-positive-coverage-incomplete")
        if not docker_ok: reasons.extend(docker_reasons)
        reasons = list(dict.fromkeys(reasons))
        results.append({
            "protocolFamilyId": family["id"], "catalogFamilyId": family_id,
            "scopedClaim": SCOPED_CLAIMS[family_id], "eligible": not reasons,
            "reasons": reasons, "scaleAndDiversity": scale,
            "promptNearDuplication": similarity, "criterionMutationCoverage": mutation,
            "equivalentPositiveCoverage": equivalent,
            "dockerCalibration": {"passed": docker_ok},
        })
    report = {
        "schemaVersion": 1, "recordKind": "construct-readiness",
        "protocolId": protocol["protocolId"], "protocolHash": protocol_hash,
        "catalogHash": catalog_hash,
        "thresholds": {
            "minimumConfirmatoryFixtures": MIN_CONFIRMATORY_FIXTURES,
            "minimumEcosystems": MIN_ECOSYSTEMS, "minimumSurfaces": MIN_SURFACES,
            "maximumPromptTrigramJaccard": MAX_PROMPT_TRIGRAM_JACCARD,
            "minimumCriterionMutationCoverage": MIN_CRITERION_MUTATION_COVERAGE,
            "minimumEquivalentPositivesPerFixture": MIN_EQUIVALENT_POSITIVES_PER_FIXTURE,
        },
        "sourceArtifacts": {"dockerCalibration": calibration},
        "campaignEligible": all(item["eligible"] for item in results),
        "families": results,
    }
    report["reportHash"] = value_hash(report)
    return report


def validate_report(report: Any, protocol: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    # Published reports intentionally exclude bulky source artifacts. Recompute structural
    # metrics, then validate the frozen self-contained result contract below.
    required = {
        "schemaVersion", "recordKind", "protocolId", "protocolHash", "catalogHash",
        "thresholds", "sourceArtifacts", "campaignEligible", "families", "reportHash",
    }
    if not isinstance(report, dict) or set(report) != required \
            or report.get("schemaVersion") != 1 or report.get("recordKind") != "construct-readiness":
        raise ReadinessError("invalid construct-readiness report shape")
    unsigned = {key: value for key, value in report.items() if key != "reportHash"}
    if report["reportHash"] != value_hash(unsigned):
        raise ReadinessError("construct-readiness report hash mismatch")
    if report["protocolId"] != protocol["protocolId"] \
            or report["protocolHash"] != value_hash(protocol) \
            or report["catalogHash"] != value_hash(catalog):
        raise ReadinessError("construct-readiness report source hash mismatch")
    expected_thresholds = {
        "minimumConfirmatoryFixtures": MIN_CONFIRMATORY_FIXTURES,
        "minimumEcosystems": MIN_ECOSYSTEMS, "minimumSurfaces": MIN_SURFACES,
        "maximumPromptTrigramJaccard": MAX_PROMPT_TRIGRAM_JACCARD,
        "minimumCriterionMutationCoverage": MIN_CRITERION_MUTATION_COVERAGE,
        "minimumEquivalentPositivesPerFixture": MIN_EQUIVALENT_POSITIVES_PER_FIXTURE,
    }
    if report["thresholds"] != expected_thresholds:
        raise ReadinessError("construct-readiness thresholds differ from implementation")
    sources = report["sourceArtifacts"]
    if not isinstance(sources, dict) or set(sources) != {"dockerCalibration"}:
        raise ReadinessError("construct-readiness source artifacts are missing")
    recomputed = build_report(protocol, catalog, calibration=sources["dockerCalibration"])
    if canonical_json(recomputed) != canonical_json(report):
        raise ReadinessError("construct-readiness report does not reproduce from its sources")
    expected_family_ids = [family["id"] for family in protocol["families"]]
    if [item.get("protocolFamilyId") for item in report["families"]] != expected_family_ids:
        raise ReadinessError("construct-readiness family coverage mismatch")
    for family in report["families"]:
        checks = (
            family.get("scaleAndDiversity", {}).get("passed"),
            family.get("promptNearDuplication", {}).get("passed"),
            family.get("criterionMutationCoverage", {}).get("passed"),
            family.get("equivalentPositiveCoverage", {}).get("passed"),
            family.get("dockerCalibration", {}).get("passed"),
        )
        if family.get("eligible") is not all(check is True for check in checks):
            raise ReadinessError("construct-readiness eligibility is inconsistent")
        if family.get("eligible") and family.get("reasons"):
            raise ReadinessError("eligible family cannot contain readiness failures")
    if report["campaignEligible"] is not all(item.get("eligible") is True for item in report["families"]):
        raise ReadinessError("campaign eligibility is inconsistent")
    return report


def assert_campaign_ready(report: Any, protocol: dict[str, Any], catalog: dict[str, Any]) -> None:
    validate_report(report, protocol, catalog)
    failures = {
        family["protocolFamilyId"]: family["reasons"]
        for family in report["families"] if not family["eligible"]
    }
    if report["campaignEligible"] is not True or failures:
        raise ReadinessError(
            "paid/promoting campaign blocked by construct-validity gate: "
            + canonical_json(failures)
        )


def assert_families_ready(
    report: Any, protocol: dict[str, Any], catalog: dict[str, Any],
    protocol_family_ids: set[str],
) -> None:
    """Authorize promotion only for explicitly requested, construct-ready families."""
    validate_report(report, protocol, catalog)
    indexed = {item["protocolFamilyId"]: item for item in report["families"]}
    unknown = protocol_family_ids - set(indexed)
    failures = {
        family_id: indexed[family_id]["reasons"]
        for family_id in sorted(protocol_family_ids - unknown)
        if indexed[family_id]["eligible"] is not True
    }
    if unknown or failures:
        raise ReadinessError(
            "policy promotion blocked by construct-validity gate: "
            + canonical_json({"unknown": sorted(unknown), "failures": failures})
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    report_parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    report_parser.add_argument("--docker-calibration", type=Path)
    report_parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    calibration_parser = sub.add_parser("calibrate-docker")
    calibration_parser.add_argument("--output", type=Path, required=True)
    check_parser = sub.add_parser("check")
    check_parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    check_parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    check_parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if args.command == "calibrate-docker":
        catalog = load_json(DEFAULT_CATALOG)
        attempt = routing_tasks.harness.execute_captured(
            ["docker", "image", "inspect", "--format={{.Id}}",
             routing_tasks.ROUTING_EVALUATOR_IMAGE],
            cwd=ROOT, environment=None, timeout=30, limit=4096,
        )
        image_id = attempt["stdout"].strip()
        if attempt["exitCode"] != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ReadinessError("cannot bind Docker calibration to evaluator image ID")
        raw = routing_tasks.calibrate(backend="docker")
        artifact = {
            "schemaVersion": 1, "recordKind": "routing-calibration", "backend": "docker",
            "catalogHash": value_hash(catalog),
            "evaluatorImage": routing_tasks.ROUTING_EVALUATOR_IMAGE,
            "evaluatorImageId": image_id, "passed": raw["passed"], "cases": raw["cases"],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"passed": artifact["passed"], "artifactHash": value_hash(artifact)}))
        return 0 if artifact["passed"] else 1
    protocol, catalog = load_json(args.protocol), load_json(args.catalog)
    if args.command == "report":
        calibration = load_json(args.docker_calibration) if args.docker_calibration else None
        report = build_report(protocol, catalog, calibration=calibration)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"campaignEligible": report["campaignEligible"], "reportHash": report["reportHash"]}))
        return 0 if report["campaignEligible"] else 1
    report = load_json(args.report)
    assert_campaign_ready(report, protocol, catalog)
    print(json.dumps({"campaignEligible": True, "reportHash": report["reportHash"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
