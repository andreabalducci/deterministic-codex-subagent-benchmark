import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import routing_comparison_evidence as evidence
import routing_campaign as routing
import routing_runner


def rehash(snapshot):
    snapshot['contentHash'] = routing.value_hash({k: v for k, v in snapshot.items() if k != 'contentHash'})
    return snapshot


class ComparisonEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = routing.load_json(evidence.DEFAULT_SNAPSHOT)

    def test_current_evidence_yields_scoped_choices_and_abstains(self):
        choices = {r['familyId']: r for r in evidence.recommendations(self.snapshot)}
        self.assertEqual('astra-low', choices['mechanical-repository-work']['latencyOption'])
        self.assertEqual('sol-medium', choices['mechanical-repository-work']['observedCostOption'])
        for family in ('isolated-implementation-debugging', 'read-heavy-exploration-synthesis'):
            self.assertIsNone(choices[family]['latencyOption'])
            self.assertIsNone(choices[family]['observedCostOption'])
        self.assertFalse(self.snapshot['policyPromotionAllowed'])

    def test_changed_evidence_changes_choices_without_model_specific_rules(self):
        family = self.snapshot['report']['families'][0]
        for t in family['treatments']:
            t['uncachedStandardCostProxyUsdPerSuccess'] = 1 if t['treatmentId'] == 'astra-low' else 2
        self.snapshot['report']['analysis']['families'][0]['decision'] = 'INCONCLUSIVE'
        choice = evidence.recommendations(rehash(self.snapshot))[0]
        self.assertEqual('astra-low', choice['observedCostOption'])
        self.assertIsNone(choice['latencyOption'])

    def test_unknown_cost_never_wins_and_quality_claim_is_not_latency(self):
        self.snapshot['report']['families'][0]['treatments'][0]['uncachedStandardCostProxyUsdPerSuccess'] = None
        self.snapshot['report']['analysis']['families'][0]['decisionGate']['metric'] = 'quality-gain'
        choice = evidence.recommendations(rehash(self.snapshot))[0]
        self.assertIsNone(choice['observedCostOption'])
        self.assertIsNone(choice['latencyOption'])

    def test_corruption_missing_audit_and_wrong_protocol_fail_closed(self):
        for mutate, hash_again in (
            (lambda s: s['report'].update(planHash='f'*64), False),
            (lambda s: s['audit'].pop(), True),
            (lambda s: s['report'].update(protocolHash='f'*64), True),
        ):
            value = copy.deepcopy(self.snapshot)
            mutate(value)
            with self.assertRaises(ValueError):
                evidence.recommendations(rehash(value) if hash_again else value)

    def test_collection_checks_cohort_before_replay(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(evidence.astra_comparison, 'summarize') as summary, \
                mock.patch.object(evidence.routing_evidence, 'sanitize_results', return_value=([], [])), \
                mock.patch.object(evidence, 'audit_record') as audit:
            summary.side_effect = ValueError('incomplete cohort')
            with self.assertRaisesRegex(ValueError, 'incomplete cohort'):
                evidence.collect({}, {}, {}, Path(tmp))
            audit.assert_not_called()

    def test_reused_audit_requires_unchanged_candidate_and_keeps_generation_failure(self):
        raw = b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":1}}\n'
        attempt = dict(exitCode=1, durationSeconds=1, launcherFailure=False, timedOut=False, outputLimitExceeded=False)
        row = dict(runId='a'*16, fixtureId='fixture', fixtureManifestHash='b'*64,
                   transcriptHash=evidence.hashlib.sha256(raw).hexdigest(),
                   generation={'attemptHash': routing.value_hash(attempt), 'observedModel': None, 'serviceTier': None},
                   generationDurationSeconds=1, usage=routing_runner.parse_transcript(raw)['usage'],
                   candidateHash=None, failureKind='generation-exit', status='CANDIDATE_FAILURE')
        cache = dict(runId=row['runId'], recordHash=routing.value_hash(row), candidateHash='c'*64,
                     recordedStatus='CANDIDATE_FAILURE', replayedArtifactStatus='PASS')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'transcripts').mkdir()
            (root / 'transcripts' / f"{row['runId']}.jsonl").write_bytes(raw)
            (root / 'transcripts' / f"{row['runId']}.meta.json").write_text(json.dumps(attempt))
            with mock.patch.object(evidence.routing_tasks, 'task_by_id', return_value={'manifestHash': 'b'*64}), \
                    mock.patch.object(evidence.routing_tasks, 'verify_template_manifest'), \
                    mock.patch.object(evidence.routing_tasks, 'candidate_hash', return_value='c'*64) as digest, \
                    mock.patch.object(evidence.routing_tasks, 'candidate_integrity', return_value=[]), \
                    mock.patch.object(evidence.routing_tasks, 'evaluate_artifact') as replay:
                result = evidence.audit_record(row, root, {}, cache)
                self.assertEqual('CANDIDATE_FAILURE', result['recordedStatus'])
                self.assertEqual('PASS', result['replayedArtifactStatus'])
                replay.assert_not_called()
                replay.return_value = {'candidateHash': 'c'*64, 'status': 'PASS'}
                self.assertEqual(result, evidence.audit_record(row, root, {}))
                replay.assert_called_once()
                digest.return_value = 'd'*64
                with self.assertRaisesRegex(ValueError, 'no longer matches'):
                    evidence.audit_record(row, root, {}, cache)


if __name__ == '__main__':
    unittest.main()
