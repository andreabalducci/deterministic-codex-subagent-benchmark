#!/usr/bin/env python3
"""Collect a complete comparison cohort and derive scoped skill guidance.

No model requests are made. Private candidates/transcripts remain in the run
directory; the portable snapshot contains aggregates and artifact commitments.
This is comparative evidence, not cheapest-sufficient policy promotion.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import astra_comparison
import harness
import routing_campaign as routing
import routing_evidence
import routing_runner
import routing_tasks

ROOT = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = ROOT / 'skills/orchestrate/references/comparison-evidence.json'


def audit_record(row, run_root, catalog, cached=None):
    run_id = row['runId']
    transcript = (run_root / 'transcripts' / f'{run_id}.jsonl').read_bytes()
    attempt = routing.load_json(run_root / 'transcripts' / f'{run_id}.meta.json')
    parsed = routing_runner.parse_transcript(transcript)
    if hashlib.sha256(transcript).hexdigest() != row['transcriptHash']:
        raise ValueError(f'Transcript hash mismatch: {run_id}')
    if routing.value_hash(attempt) != row['generation']['attemptHash']:
        raise ValueError(f'Attempt hash mismatch: {run_id}')
    if parsed['usage'] != row['usage']:
        raise ValueError(f'Token usage mismatch: {run_id}')
    for field in ('observedModel', 'serviceTier'):
        if parsed[field] != row['generation'][field]:
            raise ValueError(f'Transcript runtime mismatch: {run_id}')
    if attempt['durationSeconds'] != row['generationDurationSeconds']:
        raise ValueError(f'Generation duration mismatch: {run_id}')
    task = routing_tasks.task_by_id(row['fixtureId'], catalog)
    routing_tasks.verify_template_manifest(task)
    if task['manifestHash'] != row['fixtureManifestHash']:
        raise ValueError(f'Fixture hash mismatch: {run_id}')
    candidate = run_root / 'workspaces' / run_id
    if cached is None:
        replay = routing_tasks.evaluate_artifact(
            row['fixtureId'], candidate, catalog=catalog, backend='docker')
    else:
        digest = routing_tasks.candidate_hash(task, candidate)
        if cached['recordHash'] != routing.value_hash(row) \
                or cached['recordedStatus'] != row['status'] \
                or cached['candidateHash'] != digest \
                or routing_tasks.candidate_integrity(task, candidate):
            raise ValueError(f'Cached replay no longer matches artifacts: {run_id}')
        replay = {'candidateHash': digest, 'status': cached['replayedArtifactStatus']}
        if replay['status'] not in {'PASS', 'FAIL'}:
            raise ValueError(f'Invalid cached replay status: {run_id}')
    failure = routing_runner._generation_failure({'execution': attempt}, parsed)
    if failure is None:
        failure = None if replay['status'] == 'PASS' else 'evaluation-failed'
    if failure != row['failureKind']:
        raise ValueError(f'Replayed outcome mismatch: {run_id}')
    # A generation-exit failure may preserve a passing artifact. Keep it failed.
    if row['candidateHash'] is not None and replay['candidateHash'] != row['candidateHash']:
        raise ValueError(f'Candidate hash mismatch: {run_id}')
    return {'runId': run_id, 'recordHash': routing.value_hash(row),
            'transcriptHash': row['transcriptHash'], 'attemptHash': row['generation']['attemptHash'],
            'candidateHash': replay['candidateHash'], 'recordedStatus': row['status'],
            'replayedArtifactStatus': replay['status']}


def collect(protocol, plan, pricing, run_root, *, progress=None, replay_audit=None):
    paths = sorted((run_root / 'results').glob('*.json'))
    rows, _ = routing_evidence.sanitize_results(
        [routing.load_json(path) for path in paths], plan, protocol)
    # Fail on missing, duplicated, foreign or unresolved runs before replay.
    report = astra_comparison.summarize(protocol, plan, rows, pricing)
    cached = {}
    if replay_audit is not None:
        if replay_audit['protocolHash'] != report['protocolHash'] \
                or replay_audit['planHash'] != report['planHash'] \
                or replay_audit['verifiedRecords'] != len(rows):
            raise ValueError('Cached replay audit bindings mismatch')
        cached = {item['runId']: item for item in replay_audit['results']}
        if len(cached) != len(replay_audit['results']) or set(cached) != {r['runId'] for r in rows}:
            raise ValueError('Cached replay audit coverage mismatch')
    catalog = routing_tasks.load_catalog()
    audit = []
    for index, row in enumerate(rows):
        audit.append(audit_record(row, run_root, catalog, cached.get(row['runId'])))
        if progress and (index + 1) % 18 == 0:
            progress(f'Verified {index + 1}/{len(rows)} records')
    payload = {'schemaVersion': 1, 'recordKind': 'routing-comparison-evidence',
               'protocol': protocol, 'pricing': pricing, 'report': report,
               'audit': audit, 'policyPromotionAllowed': False,
               'replayBasis': 'fresh-docker-replay' if replay_audit is None else 'hash-verified-prior-replay',
               'priorReplayHash': routing.value_hash(replay_audit) if replay_audit is not None else None}
    return {**payload, 'contentHash': routing.value_hash(payload)}


def validate_snapshot(snapshot):
    expected = {'schemaVersion', 'recordKind', 'protocol', 'pricing', 'report',
                'audit', 'policyPromotionAllowed', 'contentHash', 'replayBasis', 'priorReplayHash'}
    if set(snapshot) != expected or snapshot['schemaVersion'] != 1 \
            or snapshot['recordKind'] != 'routing-comparison-evidence' \
            or snapshot['policyPromotionAllowed'] is not False:
        raise ValueError('Invalid comparison evidence identity')
    payload = {key: value for key, value in snapshot.items() if key != 'contentHash'}
    if routing.value_hash(payload) != snapshot['contentHash']:
        raise ValueError('Comparison evidence content hash mismatch')
    protocol, report = snapshot['protocol'], snapshot['report']
    routing.validate_protocol(protocol)
    if protocol['selection']['objective'] != 'paired-model-comparison' \
            or report['policyPromotionAllowed'] is not False \
            or report['protocolHash'] != routing.value_hash(protocol) \
            or report['pricingHash'] != routing.value_hash(snapshot['pricing']) \
            or report['analysis']['complete'] is not True \
            or report['analysis']['protocolHash'] != report['protocolHash'] \
            or report['analysis']['planHash'] != report['planHash']:
        raise ValueError('Comparison evidence bindings mismatch')
    family_ids = [family['id'] for family in protocol['families']]
    for families in (report['families'], report['analysis']['families']):
        if [f['familyId'] for f in families] != family_ids:
            raise ValueError('Comparison family coverage mismatch')
    expected_count = sum(len(f['heldOutFixtureIds']) for f in protocol['families']) \
        * protocol['replicatesPerFixture'] * len(protocol['matrix'])
    if len(snapshot['audit']) != expected_count \
            or len({r['runId'] for r in snapshot['audit']}) != expected_count:
        raise ValueError('Comparison audit coverage mismatch')


def recommendations(snapshot):
    """No model names or winning families are encoded in this decision rule."""
    validate_snapshot(snapshot)
    report, protocol = snapshot['report'], snapshot['protocol']
    analysis = {f['familyId']: f for f in report['analysis']['families']}
    observations = {f['familyId']: f for f in report['families']}
    output = []
    for family in protocol['families']:
        measured = observations[family['id']]['treatments']
        decision = analysis[family['id']]['decision']
        # Cost ratios are descriptive and meaningful only for tested settings
        # that meet the observed floor. Unknown costs never become free.
        eligible = [t for t in measured if t['passRate'] >= family['absoluteQualityFloor']]
        costs_known = bool(eligible) and all(
            t['uncachedStandardCostProxyUsdPerSuccess'] is not None for t in eligible)
        cost_choice = min(eligible, key=lambda t: t['uncachedStandardCostProxyUsdPerSuccess']) \
            if costs_known else None
        output.append({'familyId': family['id'], 'scope': family['label'],
                       'decision': decision,
                       'latencyOption': family['candidateId'] if decision == 'SUPPORTED'
                       and analysis[family['id']]['decisionGate']['metric'] == 'generation-duration-gain-fraction' else None,
                       'observedCostOption': cost_choice['treatmentId'] if cost_choice else None})
    return output


def render_guidance(snapshot, *, evidence_link="references/comparison-evidence.json"):
    choices = recommendations(snapshot)
    matrix = {t['id']: t for t in snapshot['protocol']['matrix']}

    def setting(identifier):
        if identifier is None:
            return 'Insufficient evidence'
        item = matrix[identifier]
        return f"`{item['model']}` / `{item['reasoningEffort']}`"

    lines = ['## Measured comparison guidance', '',
             'For work matching the tested scope below, use the observed cost option when cost matters; '
             'use the latency option when speed matters and deterministic acceptance checks are available. '
             'These scoped options take precedence over provisional defaults for matching work only. '
             'If a measured option is unavailable, return to the matching provisional route and its availability fallbacks; '
             'report the actual configuration used and retain its hypothesis status.', '',
             '| Tested scope | Observed cost option | Supported latency option | Decision |',
             '| --- | --- | --- | --- |']
    for item in choices:
        lines.append(f"| {item['scope']} | {setting(item['observedCostOption'])} | "
                     f"{setting(item['latencyOption'])} | {item['decision']} |")
    lines.extend(['',
        f"Pricing snapshot: {snapshot['pricing']['asOf']}; uncached Standard API proxy per verified success, including failed attempts. "
        'Cost choices are descriptive among tested settings meeting the observed quality floor; they are not certified cheapest-sufficient routes or subscription-credit estimates.',
        'Inconclusive or contradicted latency claims provide no latency recommendation. '
        'Insufficient evidence does not prove that an untested fallback is better. '
        'Exact source-record, JSON integration and rollback-state tests do not establish broad code-analysis, security, concurrency or live-coordinator capability.',
        f"Evidence: [comparison-evidence.json]({evidence_link}), content SHA-256 `{snapshot['contentHash']}`. "
        f"Replay basis: {snapshot['replayBasis']}. The snapshot records local artifact replay commitments; rechecking private originals requires the collection command. "
        'It does not authorize cheapest-sufficient policy promotion.', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=astra_comparison.PROTOCOL)
    parser.add_argument('--pricing', type=Path, default=astra_comparison.PRICES)
    parser.add_argument('--output', type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument('--skill', type=Path, default=ROOT / 'skills/orchestrate/SKILL.md')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--reuse-replay-audit', type=Path,
                        help='Reuse a trusted prior replay after checking record, transcript and candidate hashes')
    args = parser.parse_args()
    if args.output.resolve() != (args.skill.parent / 'references/comparison-evidence.json').resolve():
        parser.error('--output must be references/comparison-evidence.json beside --skill')
    snapshot = collect(routing.load_json(args.protocol), routing.load_json(args.plan),
                       routing.load_json(args.pricing), args.run_root,
                       progress=lambda message: print(message, flush=True),
                       replay_audit=routing.load_json(args.reuse_replay_audit) if args.reuse_replay_audit else None)
    validate_snapshot(snapshot)
    if args.check:
        if routing.load_json(args.output) != snapshot:
            raise ValueError('Collected evidence is out of date')
    # Validate and render everything before replacing any output.
    import routing_policy
    policy = routing_policy.load_json(routing_policy.DEFAULT_ARTIFACT)
    routing_policy.validate_policy(policy, routing_policy.load_matrix())
    rendered = routing_policy.render_skill(
        routing_policy.DEFAULT_TEMPLATE.read_text(), policy, comparison=snapshot)
    reference = args.skill.parent / 'references/model-routing.md'
    routing_text = routing_policy.render_routing_reference(policy, comparison=snapshot)
    if args.check:
        if args.skill.read_text() != rendered or not reference.is_file() or reference.read_text() != routing_text:
            raise ValueError('Generated comparison skill is out of date')
    else:
        harness.atomic_write(args.output, (routing.canonical_json(snapshot) + '\n').encode(), replace=True)
        harness.atomic_write(reference, routing_text.encode(), replace=True)
        harness.atomic_write(args.skill, rendered.encode(), replace=True)
    print('Comparison evidence and skill are synchronized')


if __name__ == '__main__':
    main()
