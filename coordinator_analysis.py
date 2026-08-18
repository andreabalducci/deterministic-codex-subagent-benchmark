#!/usr/bin/env python3
"""Analyze the complete live-coordinator cohort without selecting post-hoc winners."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

import coordinator_campaign
import coordinator_runner
import routing_campaign
import routing_tasks

ROOT = Path(__file__).resolve().parent
ANALYSIS_SCHEMA = ROOT / "schemas" / "coordinator-analysis.schema.json"


def _success(result: dict[str, Any]) -> bool:
    """The primary outcome jointly requires trace compliance and integration acceptance."""
    return result["status"] == "PASS" \
        and result["coordination"]["traceCompliance"] is True \
        and result["integration"]["accepted"] is True


def validate_complete_results(results: Any, protocol: dict[str, Any],
                              plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(results, list):
        raise coordinator_campaign.ValidationError("coordinator results must be an array")
    by_id: dict[str, dict[str, Any]] = {}
    for index, result in enumerate(results):
        try:
            coordinator_runner.validate_result(result, protocol, plan)
        except (TypeError, ValueError, KeyError) as error:
            raise coordinator_campaign.ValidationError(
                f"invalid coordinator result at index {index}: {error}"
            ) from error
        if result["runId"] in by_id:
            raise coordinator_campaign.ValidationError("duplicate coordinator result")
        by_id[result["runId"]] = result
    expected = {job["runId"] for job in plan["jobs"]}
    missing, extra = expected - set(by_id), set(by_id) - expected
    unresolved = {run_id for run_id, result in by_id.items()
                  if result["status"] == "INFRA_FAILURE"}
    if missing or extra or unresolved:
        raise coordinator_campaign.ValidationError(
            f"coordinator decisions require a complete resolved cohort; "
            f"missing={len(missing)}, extra={len(extra)}, unresolved={len(unresolved)}"
        )
    return by_id


def _group(results: dict[str, dict[str, Any]], protocol: dict[str, Any]) \
        -> dict[str, dict[int, dict[str, dict[str, Any]]]]:
    grouped = {fixture: {} for fixture in protocol["fixtureIds"]}
    treatments = {item["id"] for item in protocol["coordinatorTreatments"]}
    for result in results.values():
        grouped[result["fixtureId"]].setdefault(result["replicate"], {})[
            result["coordinatorTreatmentId"]
        ] = result
    for fixture, replicates in grouped.items():
        if len(replicates) != protocol["replicatesPerFixture"] \
                or any(set(block) != treatments for block in replicates.values()):
            raise coordinator_campaign.ValidationError(
                f"incomplete paired coordinator block: {fixture}"
            )
    return grouped


def _rate(grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
          fixtures: list[str], treatment: str) -> float:
    fixture_rates = []
    for fixture in fixtures:
        values = [_success(grouped[fixture][replicate][treatment])
                  for replicate in sorted(grouped[fixture])]
        fixture_rates.append(sum(values) / len(values))
    return sum(fixture_rates) / len(fixture_rates)


def _bootstrap(grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
               fixtures: list[str], treatments: list[str], candidate: str,
               samples: int, seed: str) -> tuple[list[float], dict[str, list[float]]]:
    rng = random.Random(int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16))
    candidate_draws: list[float] = []
    contrast_draws = {item: [] for item in treatments if item != candidate}
    for _ in range(samples):
        rates = {item: [] for item in treatments}
        for _fixture_draw in fixtures:
            fixture = fixtures[rng.randrange(len(fixtures))]
            replicate_ids = sorted(grouped[fixture])
            drawn = [replicate_ids[rng.randrange(len(replicate_ids))]
                     for _ in replicate_ids]
            for treatment in treatments:
                rates[treatment].append(sum(
                    _success(grouped[fixture][replicate][treatment]) for replicate in drawn
                ) / len(drawn))
        means = {item: sum(values) / len(values) for item, values in rates.items()}
        candidate_draws.append(means[candidate])
        for comparator in contrast_draws:
            contrast_draws[comparator].append(means[candidate] - means[comparator])
    return candidate_draws, contrast_draws


def _stability(grouped: dict[str, dict[int, dict[str, dict[str, Any]]]],
               protocol: dict[str, Any], catalog: dict[str, Any], candidate: str,
               decision_comparator: str) -> dict[str, Any]:
    fixtures = protocol["fixtureIds"]
    leave_one_out = []
    for omitted in fixtures:
        retained = [fixture for fixture in fixtures if fixture != omitted]
        leave_one_out.append({
            "omittedFixtureId": omitted,
            "candidateRate": _rate(grouped, retained, candidate),
            "decisionGain": _rate(grouped, retained, candidate)
            - _rate(grouped, retained, decision_comparator),
        })
    machine_rates = []
    for machine in protocol["machines"]:
        candidate_values, comparator_values = [], []
        for fixture in fixtures:
            for block in grouped[fixture].values():
                candidate_result = block[candidate]
                comparator_result = block[decision_comparator]
                if candidate_result["machineId"] == machine:
                    candidate_values.append(_success(candidate_result))
                    comparator_values.append(_success(comparator_result))
        machine_rates.append({
            "machineId": machine,
            "candidateRate": sum(candidate_values) / len(candidate_values),
            "decisionGain": (sum(candidate_values) - sum(comparator_values))
            / len(candidate_values),
        })
    by_id = {task["id"]: task for task in catalog["tasks"]}
    ecosystems: dict[str, list[str]] = {}
    for fixture in fixtures:
        ecosystems.setdefault(by_id[fixture]["ecosystem"], []).append(fixture)
    ecosystem_rates = [{
        "ecosystem": ecosystem,
        "candidateRate": _rate(grouped, scoped, candidate),
        "decisionGain": _rate(grouped, scoped, candidate)
        - _rate(grouped, scoped, decision_comparator),
    } for ecosystem, scoped in sorted(ecosystems.items())]
    threshold = float(protocol["analysis"]["minimumDecisionGain"])
    floor = float(protocol["analysis"]["absoluteQualityFloor"])
    stable = (
        all(item["candidateRate"] >= floor and item["decisionGain"] > threshold
            for item in leave_one_out)
        and all(item["candidateRate"] >= floor and item["decisionGain"] > threshold
                for item in machine_rates)
        and all(item["candidateRate"] >= floor and item["decisionGain"] > threshold
                for item in ecosystem_rates)
    )
    return {"passed": stable, "leaveOneFixtureOut": leave_one_out,
            "machines": machine_rates, "ecosystems": ecosystem_rates}


def analyze(protocol: dict[str, Any], plan: dict[str, Any], results: list[dict[str, Any]],
            *, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or routing_tasks.load_catalog()
    coordinator_campaign.validate_protocol(protocol, catalog=catalog)
    coordinator_campaign.validate_plan(plan, protocol)
    validated = validate_complete_results(results, protocol, plan)
    grouped = _group(validated, protocol)
    treatments = [item["id"] for item in protocol["coordinatorTreatments"]]
    prereg = protocol["analysis"]
    candidate = prereg["candidateId"]
    comparators = [item for item in treatments if item != candidate]
    rates = {item: _rate(grouped, protocol["fixtureIds"], item) for item in treatments}
    absolute_draws, contrast_draws = _bootstrap(
        grouped, protocol["fixtureIds"], treatments, candidate,
        protocol["bootstrapSamples"], protocol["bootstrapSeed"],
    )
    alpha = float(protocol["familywiseAlpha"])
    claim_count = len(comparators) + 2
    simultaneous_alpha = alpha / claim_count
    support_claims: list[dict[str, Any]] = []
    contradiction_claims: list[dict[str, Any]] = []
    absolute = {
        "estimate": rates[candidate], "floor": prereg["absoluteQualityFloor"],
        "lower95": routing_campaign.percentile(absolute_draws, simultaneous_alpha),
        "upper95": routing_campaign.percentile(absolute_draws, 1 - simultaneous_alpha),
        "rawSupportP": routing_campaign.centered_bootstrap_p(
            absolute_draws, rates[candidate], prereg["absoluteQualityFloor"], "greater"),
        "holmAdjustedSupportP": 1.0,
        "rawContradictionP": routing_campaign.centered_bootstrap_p(
            absolute_draws, rates[candidate], prereg["absoluteQualityFloor"], "less"),
        "holmAdjustedContradictionP": 1.0,
    }
    support_claims.append(absolute); contradiction_claims.append(absolute)
    comparisons = []
    for comparator in comparators:
        difference = rates[candidate] - rates[comparator]
        draws = contrast_draws[comparator]
        margin = prereg["nonInferiorityMargin"]
        item = {
            "comparatorId": comparator, "candidateRate": rates[candidate],
            "comparatorRate": rates[comparator], "difference": difference,
            "nonInferiorityMargin": margin,
            "lower95": routing_campaign.percentile(draws, simultaneous_alpha),
            "upper95": routing_campaign.percentile(draws, 1 - simultaneous_alpha),
            "rawSupportP": routing_campaign.centered_bootstrap_p(
                draws, difference, -margin, "greater"), "holmAdjustedSupportP": 1.0,
            "rawContradictionP": routing_campaign.centered_bootstrap_p(
                draws, difference, -margin, "less"), "holmAdjustedContradictionP": 1.0,
        }
        comparisons.append(item); support_claims.append(item); contradiction_claims.append(item)
    decision_comparator = prereg["decisionComparatorId"]
    decision_gain = rates[candidate] - rates[decision_comparator]
    decision_draws = contrast_draws[decision_comparator]
    decision_gate = {
        "metric": "trace-and-integration-success-rate-difference",
        "comparatorId": decision_comparator, "candidateRate": rates[candidate],
        "comparatorRate": rates[decision_comparator], "gain": decision_gain,
        "minimumGain": prereg["minimumDecisionGain"],
        "lower95": routing_campaign.percentile(decision_draws, simultaneous_alpha),
        "upper95": routing_campaign.percentile(decision_draws, 1 - simultaneous_alpha),
        "rawSupportP": routing_campaign.centered_bootstrap_p(
            decision_draws, decision_gain, prereg["minimumDecisionGain"], "greater"),
        "holmAdjustedSupportP": 1.0,
        "rawContradictionP": routing_campaign.centered_bootstrap_p(
            decision_draws, decision_gain, prereg["minimumDecisionGain"], "less"),
        "holmAdjustedContradictionP": 1.0,
    }
    support_claims.append(decision_gate); contradiction_claims.append(decision_gate)
    adjusted_support = routing_campaign.holm_adjust([item["rawSupportP"] for item in support_claims])
    adjusted_contradiction = routing_campaign.holm_adjust(
        [item["rawContradictionP"] for item in contradiction_claims])
    for item, adjusted in zip(support_claims, adjusted_support):
        item["holmAdjustedSupportP"] = adjusted
    for item, adjusted in zip(contradiction_claims, adjusted_contradiction):
        item["holmAdjustedContradictionP"] = adjusted
    quality_supported = absolute["lower95"] > absolute["floor"] \
        and absolute["holmAdjustedSupportP"] <= alpha
    ni_supported = all(item["lower95"] > -item["nonInferiorityMargin"]
                       and item["holmAdjustedSupportP"] <= alpha for item in comparisons)
    decision_supported = decision_gate["lower95"] > decision_gate["minimumGain"] \
        and decision_gate["holmAdjustedSupportP"] <= alpha
    contradicted = (
        absolute["upper95"] < absolute["floor"]
        and absolute["holmAdjustedContradictionP"] <= alpha
    ) or any(item["upper95"] < -item["nonInferiorityMargin"]
             and item["holmAdjustedContradictionP"] <= alpha for item in comparisons) \
        or (decision_gate["upper95"] < decision_gate["minimumGain"]
            and decision_gate["holmAdjustedContradictionP"] <= alpha)
    stability = _stability(grouped, protocol, catalog, candidate, decision_comparator)
    decision = "CONTRADICTED" if contradicted else (
        "SUPPORTED" if quality_supported and ni_supported and decision_supported
        and stability["passed"] else "INCONCLUSIVE"
    )
    analysis = {
        "schemaVersion": 1, "recordKind": "coordinator-analysis",
        "protocolId": protocol["protocolId"],
        "protocolHash": coordinator_campaign.value_hash(protocol),
        "planHash": coordinator_campaign.value_hash(plan),
        "estimand": "live-coordinator-with-frozen-workers",
        "complete": True, "plannedRuns": len(plan["jobs"]),
        "observedRuns": len(validated), "primaryMetric":
        "trace-and-integration-success", "candidateId": candidate,
        "treatmentRates": rates, "absoluteQuality": absolute,
        "comparisons": comparisons, "decisionGate": decision_gate,
        "stability": stability, "multiplicity": {
            "method": "Holm plus Bonferroni simultaneous one-sided bounds",
            "claimCount": claim_count, "familywiseAlpha": alpha,
        },
        "decision": decision,
    }
    return validate_analysis(analysis, protocol, plan)


def validate_analysis(value: Any, protocol: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    required = {"schemaVersion", "recordKind", "protocolId", "protocolHash", "planHash",
                "estimand", "complete", "plannedRuns", "observedRuns", "primaryMetric",
                "candidateId", "treatmentRates", "absoluteQuality", "comparisons",
                "decisionGate", "stability", "multiplicity", "decision"}
    if not isinstance(value, dict) or set(value) != required:
        raise coordinator_campaign.ValidationError("coordinator analysis fields differ")
    if value["schemaVersion"] != 1 or value["recordKind"] != "coordinator-analysis" \
            or value["protocolId"] != protocol["protocolId"] \
            or value["protocolHash"] != coordinator_campaign.value_hash(protocol) \
            or value["planHash"] != coordinator_campaign.value_hash(plan) \
            or value["estimand"] != "live-coordinator-with-frozen-workers" \
            or value["complete"] is not True \
            or value["plannedRuns"] != len(plan["jobs"]) \
            or value["observedRuns"] != len(plan["jobs"]) \
            or value["primaryMetric"] != "trace-and-integration-success" \
            or value["candidateId"] != protocol["analysis"]["candidateId"] \
            or value["decision"] not in {"SUPPORTED", "INCONCLUSIVE", "CONTRADICTED"}:
        raise coordinator_campaign.ValidationError("coordinator analysis identity or cohort differs")
    return value


def _load_results(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else [value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=coordinator_campaign.DEFAULT_PROTOCOL)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    catalog = routing_tasks.load_catalog()
    protocol = coordinator_campaign.validate_protocol(
        coordinator_campaign.load_json(args.protocol), catalog=catalog)
    plan = coordinator_campaign.validate_plan(coordinator_campaign.load_json(args.plan), protocol)
    analysis = analyze(protocol, plan, _load_results(args.results), catalog=catalog)
    rendered = json.dumps(analysis, indent=2, sort_keys=True) + "\n"
    if args.output: args.output.write_text(rendered, encoding="utf-8")
    else: print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
