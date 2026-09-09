import copy
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import astra_comparison as astra
import routing_campaign as routing
import routing_preflight
import routing_runner
import routing_sequential
import harness


class AstraComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = routing.load_json(ROOT / 'protocols/astra-comparison-v1.json')
        cls.protocol['bootstrapSamples'] = 1000
        cls.runtime = routing_runner.load_runtime_manifest(ROOT / 'protocols/astra-runtime-v1.json')
        models = [{'id': t['model'], 'supportedReasoningEfforts': [{'reasoningEffort': t['reasoningEffort']}],
                   'serviceTiers': [{'id': 'default'}]} for t in cls.protocol['matrix']]
        image = {'tag': 'test', 'id': 'sha256:' + 'a'*64, 'repoDigests': [], 'os': 'linux', 'architecture': 'amd64', 'specHash': 'b'*64}
        reports = [routing_preflight.make_report(cls.protocol, cls.runtime, models, image, cls.runtime['codexVersion'], machine) for machine in cls.protocol['machines']]
        cls.plan = routing.make_plan(cls.protocol, b'0123456789abcdef', reports, cls.runtime)

    def results(self):
        rows = []
        for job in self.plan['jobs']:
            binding = next(b for b in self.plan['preflightBindings'] if b['machineId'] == job['machineId'])
            row = {k: job[k] for k in ('runId', 'familyId', 'fixtureId', 'replicate', 'machineId', 'orderPosition', 'treatmentId', 'model', 'reasoningEffort')}
            row.update(schemaVersion=2, recordKind='routing-result', protocolHash=routing.value_hash(self.protocol), planHash=routing.value_hash(self.plan),
                       runtimeManifestHash=self.protocol['runtimeManifestHash'], preflightReportHash=binding['reportHash'], capabilityDigest=binding['capabilityDigest'],
                       status='PASS', failureKind=None, fixtureManifestHash='a'*64, promptHash='b'*64, candidateHash='c'*64, transcriptHash='d'*64,
                       generationDurationSeconds=1.0 if job['treatmentId']=='astra-low' else 2.0, evaluationDurationSeconds=.5, totalDurationSeconds=2.5, provenance={})
            row['usage'] = dict(inputTokens=1000, outputTokens=100, cachedInputTokens=None, reasoningOutputTokens=50, cacheWriteInputTokens=None, totalTokens=1100)
            row['evaluation'] = dict(backend='native', evaluatorProfile=None, evaluatorImage=None, reportHash='e'*64)
            row['generation'] = dict(backend='docker', isolation='container-strong', generatorImage=None, codexVersion=self.runtime['codexVersion'], attemptHash='f'*64,
                                     requestedModel=job['model'], requestedReasoningEffort=job['reasoningEffort'], requestedServiceTier='default', fastMode=False,
                                     multiAgent=False, observedModel=None, serviceTier=None, runtimeVerification='cli-request-and-success')
            rows.append(row)
        return rows

    def test_paired_plan_balanced_and_bound(self):
        routing.validate_protocol_sources(self.protocol, routing.load_json(ROOT/'protocols/astra-matrix-v1.json'), routing.load_json(ROOT/'fixtures/catalog.json'))
        routing.validate_plan(self.plan, self.protocol)
        self.assertEqual(216, len(self.plan['jobs']))
        for machine in self.protocol['machines']:
            for treatment in ('sol-medium', 'astra-low'):
                for position in (0, 1):
                    self.assertEqual(18, sum(j['machineId']==machine and j['treatmentId']==treatment and j['orderPosition']==position for j in self.plan['jobs']))
        with self.assertRaises(routing_sequential.SequentialError):
            routing_sequential.make_manifest(self.plan, self.protocol)

    def test_wrong_runtime_and_missing_astra_rejected(self):
        with self.assertRaises(ValueError):
            routing.make_plan(self.protocol, b'0123456789abcdef', [], routing_runner.load_runtime_manifest())
        with self.assertRaises(routing_preflight.PreflightError):
            routing_preflight.validate_catalog(self.protocol, self.runtime, [])

    def test_isolated_runtime_disables_account_tools(self):
        self.assertEqual('astra-comparison-v2.json', astra.PROTOCOL.name)
        runtime = routing_runner.load_runtime_manifest(ROOT / 'protocols/astra-runtime-v2.json')
        arguments = routing_runner.feature_exclusion_arguments(runtime)
        for feature in ('plugins', 'remote_plugin', 'apps', 'hooks'):
            index = arguments.index(feature)
            self.assertEqual('--disable', arguments[index - 1])
        protocol = routing.load_json(ROOT / 'protocols/astra-comparison-v2.json')
        self.assertEqual(routing.value_hash(runtime), protocol['runtimeManifestHash'])
        original = {f for family in self.protocol['families'] for f in family['heldOutFixtureIds']}
        reserve = {f for family in protocol['families'] for f in family['heldOutFixtureIds']}
        self.assertFalse(original & reserve)

    def test_schema_and_protocol_reject_sequential_comparison(self):
        schema = routing.load_json(ROOT / 'schemas/routing-protocol.schema.json')
        harness.validate_schema_instance(self.protocol, schema, root_schema=schema)
        invalid = copy.deepcopy(self.protocol)
        invalid['selection']['stageDecision'] = 'accept-or-escalate'
        with self.assertRaises(ValueError):
            routing.validate_protocol(invalid)
        with self.assertRaises(ValueError):
            harness.validate_schema_instance(invalid, schema, root_schema=schema)

    def test_cost_report_rejects_mixed_tiers_and_unknown_cost_is_null(self):
        rows = self.results()
        rows[0]['usage']['inputTokens'] = None
        report = astra.summarize(self.protocol, self.plan, rows, routing.load_json(astra.PRICES))
        treatment = next(t for f in report['families'] if f['familyId']==rows[0]['familyId']
                         for t in f['treatments'] if t['treatmentId']==rows[0]['treatmentId'])
        self.assertIsNone(treatment['uncachedStandardCostProxyUsdPerSuccess'])
        rows[0]['generation']['requestedServiceTier'] = 'priority'
        rows[0]['generation']['fastMode'] = True
        with self.assertRaisesRegex(ValueError, 'same service tier'):
            astra.summarize(self.protocol, self.plan, rows, routing.load_json(astra.PRICES))

    def test_cost_proxy_and_complete_report(self):
        prices = routing.load_json(astra.PRICES)
        report = astra.summarize(self.protocol, self.plan, self.results(), prices)
        a, b = report['families'][0]['treatments']
        self.assertAlmostEqual(2.5, b['uncachedStandardCostProxyUsdPerSuccess']/a['uncachedStandardCostProxyUsdPerSuccess'])
        self.assertFalse(report['policyPromotionAllowed'])
        self.assertTrue(all(f['decision']=='SUPPORTED' for f in report['analysis']['families']))
        # Unknown usage is not free, and reasoning must not be billed twice.
        self.assertIsNone(astra.token_cost({'inputTokens':None,'outputTokens':100}, prices['models']['gpt-6-astra']))
        self.assertAlmostEqual(.015, astra.token_cost({'inputTokens':1000,'outputTokens':100,'reasoningOutputTokens':50}, prices['models']['gpt-6-astra']))

    def test_failures_remain_in_cost_and_incomplete_cohorts_fail(self):
        prices = routing.load_json(astra.PRICES)
        rows = self.results()
        with self.assertRaises(routing.ValidationError):
            astra.summarize(self.protocol,self.plan,rows[:-1],prices)
        rows[0]['status']='CANDIDATE_FAILURE'; rows[0]['failureKind']='evaluation-failed'
        report=astra.summarize(self.protocol,self.plan,rows,prices)
        family=next(f for f in report['families'] if f['familyId']==rows[0]['familyId'])
        item=next(t for t in family['treatments'] if t['treatmentId']==rows[0]['treatmentId'])
        self.assertEqual(17,item['verifiedSuccesses'])
        self.assertAlmostEqual(item['uncachedStandardCostProxyUsd']/17,item['uncachedStandardCostProxyUsdPerSuccess'])
        rows[0]['status']='INFRA_FAILURE'
        with self.assertRaises(routing.ValidationError):
            astra.summarize(self.protocol,self.plan,rows,prices)


if __name__ == '__main__':
    unittest.main()
