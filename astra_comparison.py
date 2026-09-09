#!/usr/bin/env python3
"""Replay a complete paired cohort and report quality, latency and token-cost proxies.

API-equivalent costs are normalized Standard-rate scenarios, never Codex invoices.
All sampled failures remain in the numerator; reasoning is already in output tokens.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import routing_campaign as routing

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / 'protocols/astra-comparison-v2.json'
PRICES = ROOT / 'protocols/astra-pricing-2026-09-07.json'


def token_cost(usage, rates):
    """Return an uncached Standard-price proxy, or None if totals are unknown.

    Ignoring cache discounts/writes deliberately avoids treating CLI's absent cache
    telemetry (which older runners encode as zero) as an observed billing fact.
    """
    counts = [usage.get('inputTokens'), usage.get('outputTokens')]
    if any(value is None for value in counts):
        return None
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError('Invalid token counts')
    if any(type(rates.get(key)) not in (int, float) or not math.isfinite(rates[key]) or rates[key] < 0
           for key in ('inputPerMillion', 'outputPerMillion')):
        raise ValueError('Invalid token prices')
    return (counts[0] * rates['inputPerMillion'] + counts[1] * rates['outputPerMillion']) / 1_000_000


def summarize(protocol, plan, results, pricing):
    if protocol['selection']['objective'] != 'paired-model-comparison':
        raise ValueError('Expected a complete paired comparison protocol')
    if any(r.get('schemaVersion') != 2 or r.get('totalDurationSeconds') is None for r in results):
        raise ValueError('Cost comparison requires v2 results with total duration')
    # This validates plan membership, duplicate/missing results, unresolved infra,
    # runtime provenance, and recomputes all preregistered quality/latency gates.
    analysis = routing.analyze(protocol, plan, results)
    if len({r['generation']['requestedServiceTier'] for r in results}) != 1:
        raise ValueError('Compared treatments must use the same service tier')
    families = []
    for family in protocol['families']:
        treatments = []
        for treatment in protocol['matrix']:
            cohort = [r for r in results if r['familyId'] == family['id'] and r['treatmentId'] == treatment['id']]
            costs = [token_cost(r['usage'], pricing['models'][treatment['model']]) for r in cohort]
            known = all(c is not None for c in costs)
            successes = sum(r['status'] == 'PASS' for r in cohort)
            duration = sum(r['totalDurationSeconds'] for r in cohort)
            generation = sorted(r['generationDurationSeconds'] for r in cohort)
            treatments.append({
                'treatmentId': treatment['id'], 'samples': len(cohort),
                'verifiedSuccesses': successes, 'passRate': successes / len(cohort),
                'generationP50Seconds': routing.percentile(generation, .5),
                'generationP95Seconds': routing.percentile(generation, .95),
                'totalSecondsPerSuccess': duration / successes if successes else None,
                'uncachedStandardCostProxyUsd': sum(costs) if known else None,
                'uncachedStandardCostProxyUsdPerSuccess': sum(costs) / successes if known and successes else None,
            })
        families.append({'familyId': family['id'], 'treatments': treatments})
    return {
        'schemaVersion': 1, 'recordKind': 'astra-comparison-report',
        'protocolHash': routing.value_hash(protocol), 'planHash': routing.value_hash(plan),
        'pricingHash': routing.value_hash(pricing), 'analysis': analysis,
        'families': families, 'policyPromotionAllowed': False,
        'costBasis': 'uncached-standard-short-context-proxy-not-invoice',
        'limitations': [
            'Cost proxies exclude cache discounts/writes, long-context surcharges, service-tier differences, tool fees and infrastructure replacements.',
            'Codex subscription credits cannot be inferred from API prices.',
            'Cost-per-success ratios are descriptive; they are not a retry-policy simulation or confidence bound.',
            'Worker evidence does not establish a live coordinator default or broad security/concurrency capability.',
            'SUPPORTED in the embedded analysis means quality and generation-latency support, not monetary savings or policy promotion.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', nargs='+', type=Path)
    parser.add_argument('--protocol', type=Path, default=PROTOCOL)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--pricing', type=Path, default=PRICES)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = summarize(routing.load_json(args.protocol), routing.load_json(args.plan),
                       [routing.load_json(p) for p in args.results], routing.load_json(args.pricing))
    routing.save_json(args.output, report)


if __name__ == '__main__':
    main()
