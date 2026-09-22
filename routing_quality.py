#!/usr/bin/env python3
"""Rank complete .NET/SPA routing cohorts by verified quality, then cost and time.

The price calculation is an uncached Standard API proxy, not a Codex invoice.
Every candidate must run the same preregistered fixture and replicate blocks.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import routing_campaign as routing


ROOT = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = ROOT / "protocols" / "routing-quality-v1.json"
DEFAULT_PRICING = ROOT / "protocols" / "routing-pricing-2026-09-22.json"


def _pricing_rates(pricing: Any, models: set[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(pricing, dict) or set(pricing) != {"asOf", "currency", "basis", "models"} \
            or pricing["currency"] != "USD" or not isinstance(pricing["models"], dict) \
            or set(pricing["models"]) != models:
        raise ValueError("Pricing snapshot must cover exactly the compared models in USD")
    for model, rates in pricing["models"].items():
        if not isinstance(rates, dict) or set(rates) != {"inputPerMillion", "outputPerMillion", "source"}:
            raise ValueError(f"Invalid pricing fields for {model}")
        if not isinstance(rates["source"], str) or not rates["source"].startswith("https://developers.openai.com/"):
            raise ValueError(f"Pricing source must be an official OpenAI page for {model}")
        for field in ("inputPerMillion", "outputPerMillion"):
            value = rates[field]
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {field} for {model}")
    return pricing["models"]


def _cost_proxy(result: dict[str, Any], rates: dict[str, Any]) -> float | None:
    usage = result["usage"]
    input_tokens, output_tokens = usage["inputTokens"], usage["outputTokens"]
    if input_tokens is None or output_tokens is None:
        return None
    return (
        input_tokens * rates["inputPerMillion"]
        + output_tokens * rates["outputPerMillion"]
    ) / 1_000_000


def _scope(
    family: dict[str, Any], ecosystem: str | None, matrix: list[dict[str, str]],
    results: list[dict[str, Any]], rates: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    fixture_ids = [
        fixture_id for fixture_id, fixture_ecosystem in zip(
            family["heldOutFixtureIds"], family["heldOutFixtureEcosystems"]
        ) if ecosystem is None or fixture_ecosystem == ecosystem
    ]
    selected_results = [result for result in results if result["fixtureId"] in fixture_ids]
    treatments = []
    for treatment in matrix:
        cohort = [result for result in selected_results if result["treatmentId"] == treatment["id"]]
        successes = sum(result["status"] == "PASS" for result in cohort)
        costs = [_cost_proxy(result, rates[treatment["model"]]) for result in cohort]
        total_cost = sum(costs) if all(cost is not None for cost in costs) else None
        treatments.append({
            "treatmentId": treatment["id"],
            "model": treatment["model"],
            "reasoningEffort": treatment["reasoningEffort"],
            "samples": len(cohort),
            "verifiedSuccesses": successes,
            "verifiedPassRate": successes / len(cohort),
            "uncachedStandardCostProxyUsd": total_cost,
            "generationP50Seconds": routing.percentile(
                [result["generationDurationSeconds"] for result in cohort], 0.5
            ),
        })
    best_successes = max(item["verifiedSuccesses"] for item in treatments)
    quality_ties = [item for item in treatments if item["verifiedSuccesses"] == best_successes]
    selected = None
    status = "SELECTED"
    floor = family["absoluteQualityFloor"]
    if quality_ties[0]["verifiedPassRate"] < floor:
        status = "BELOW_QUALITY_FLOOR"
    elif len(quality_ties) > 1 and any(
        item["uncachedStandardCostProxyUsd"] is None for item in quality_ties
    ):
        status = "COST_DATA_MISSING"
    else:
        selected = min(
            quality_ties,
            key=lambda item: (
                item["uncachedStandardCostProxyUsd"] if item["uncachedStandardCostProxyUsd"] is not None else math.inf,
                item["generationP50Seconds"],
                next(index for index, treatment in enumerate(matrix) if treatment["id"] == item["treatmentId"]),
            ),
        )["treatmentId"]
    return {
        "familyId": family["id"],
        "ecosystem": ecosystem or "all",
        "fixtureIds": fixture_ids,
        "qualityFloor": floor,
        "treatments": treatments,
        "qualityTieIds": [item["treatmentId"] for item in quality_ties],
        "selectedTreatmentId": selected,
        "selectionStatus": status,
    }


def summarize(
    protocol: dict[str, Any], plan: dict[str, Any], results: list[dict[str, Any]],
    pricing: dict[str, Any],
) -> dict[str, Any]:
    routing.validate_protocol(protocol)
    if protocol["selection"]["objective"] != "quality-first-complete-cohort":
        raise ValueError("Quality routing requires a complete quality-first protocol")
    routing.validate_plan(plan, protocol)
    by_id = routing.validate_results(results, plan, protocol)
    planned_ids = {job["runId"] for job in plan["jobs"]}
    if set(by_id) != planned_ids or any(result["status"] == "INFRA_FAILURE" for result in by_id.values()):
        raise ValueError("Quality routing requires a complete, resolved result cohort")
    if any(result["schemaVersion"] != 2 for result in by_id.values()):
        raise ValueError("Quality routing requires provenance-bound v2 results")
    if len({result["generation"]["requestedServiceTier"] for result in by_id.values()}) != 1:
        raise ValueError("Every treatment must use the same service tier")
    rates = _pricing_rates(pricing, {item["model"] for item in protocol["matrix"]})
    rows = []
    for family in protocol["families"]:
        family_results = [result for result in by_id.values() if result["familyId"] == family["id"]]
        rows.append(_scope(family, None, protocol["matrix"], family_results, rates))
        for ecosystem in sorted(set(family["heldOutFixtureEcosystems"])):
            rows.append(_scope(family, ecosystem, protocol["matrix"], family_results, rates))
    return {
        "schemaVersion": 1,
        "recordKind": "routing-quality-report",
        "protocolHash": routing.value_hash(protocol),
        "planHash": routing.value_hash(plan),
        "pricingHash": routing.value_hash(pricing),
        "complete": True,
        "selectionRule": "highest-verified-success-then-lowest-cost-proxy-then-fastest-generation-p50",
        "costBasis": pricing["basis"],
        "rows": rows,
        "policyPromotionAllowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = summarize(
        routing.load_json(args.protocol), routing.load_json(args.plan),
        [routing.load_json(path) for path in args.results], routing.load_json(args.pricing),
    )
    routing.save_json(args.output, report)


if __name__ == "__main__":
    main()
