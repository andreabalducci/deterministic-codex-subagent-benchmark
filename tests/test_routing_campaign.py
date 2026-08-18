import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_campaign as routing  # noqa: E402
import routing_preflight  # noqa: E402
import routing_runner  # noqa: E402


class RoutingCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen_protocol = json.loads(
            (ROOT / "protocols" / "routing-v1.json").read_text(encoding="utf-8")
        )
        cls.operational_protocol = json.loads(
            (ROOT / "protocols" / "routing-operational-v1.json").read_text(encoding="utf-8")
        )

    def small_protocol(self):
        protocol = copy.deepcopy(self.frozen_protocol)
        protocol["protocolId"] = "routing-v1-synthetic"
        protocol["bootstrapSamples"] = 1000
        protocol["replicatesPerFixture"] = 3
        protocol["machines"] = ["synthetic-machine"]
        for family in protocol["families"]:
            family["heldOutFixtureIds"] = [
                f"{family['id']}-fixture-a",
                f"{family['id']}-fixture-b",
                f"{family['id']}-fixture-c",
                f"{family['id']}-fixture-d",
                f"{family['id']}-fixture-e",
                f"{family['id']}-fixture-f",
            ]
            family["heldOutFixtureEcosystems"] = [
                "dotnet", "react-typescript", "python", "repository-artifacts",
                "dotnet", "react-typescript"
            ]
        routing.validate_protocol(protocol)
        return protocol

    def preflights(self, protocol):
        runtime = routing_runner.load_runtime_manifest()
        models = [{
            "id": model, "model": model,
            "supportedReasoningEfforts": [
                {"reasoningEffort": effort} for effort in ("low", "medium", "high")
            ],
            "serviceTiers": [{"id": "priority"}], "additionalSpeedTiers": ["fast"],
        } for model in sorted({item["model"] for item in protocol["matrix"]})]
        image = {
            "tag": "test", "id": "sha256:" + "a" * 64, "repoDigests": [],
            "os": "linux", "architecture": "amd64", "specHash": "b" * 64,
        }
        return [routing_preflight.make_report(
            protocol, runtime, models, image, runtime["codexVersion"], machine
        ) for machine in protocol["machines"]], runtime

    def plan(self, protocol, key=b"0123456789abcdef"):
        reports, runtime = self.preflights(protocol)
        return routing.make_plan(protocol, key, reports, runtime)

    def results(self, protocol, plan, scenario):
        protocol_hash = routing.value_hash(protocol)
        plan_hash = routing.value_hash(plan)
        family_by_id = {family["id"]: family for family in protocol["families"]}
        values = []
        for job in plan["jobs"]:
            family = family_by_id[job["familyId"]]
            is_candidate = job["treatmentId"] == family["candidateId"]
            is_decision_comparator = job["treatmentId"] == family["decisionComparatorId"]
            status = "PASS"
            if scenario == "contradicted" and is_candidate:
                status = "CANDIDATE_FAILURE"
            elif scenario == "supported" and family["recommendationType"] == "capability" \
                    and is_decision_comparator:
                status = "CANDIDATE_FAILURE"
            elif scenario == "inconclusive" and is_candidate \
                    and job["familyId"] == protocol["families"][0]["id"] \
                    and job["fixtureId"] == family["heldOutFixtureIds"][0] \
                    and job["replicate"] == 0:
                status = "CANDIDATE_FAILURE"
            values.append({
                "schemaVersion": 1,
                "recordKind": "routing-result",
                "protocolHash": protocol_hash,
                "planHash": plan_hash,
                "runId": job["runId"],
                "familyId": job["familyId"],
                "fixtureId": job["fixtureId"],
                "replicate": job["replicate"],
                "machineId": job["machineId"],
                "orderPosition": job["orderPosition"],
                "treatmentId": job["treatmentId"],
                "model": job["model"],
                "reasoningEffort": job["reasoningEffort"],
                "status": status,
                "generationDurationSeconds": 1.0 if is_candidate else 2.0,
                "evaluationDurationSeconds": 0.5,
            })
        return values

    def test_frozen_protocol_is_strict_and_has_3888_jobs(self):
        protocol = routing.validate_protocol(copy.deepcopy(self.frozen_protocol))
        plan = self.plan(protocol)
        self.assertEqual(3888, len(plan["jobs"]))
        self.assertEqual(6, len(protocol["families"]))
        self.assertTrue(all(len(family["heldOutFixtureIds"]) == 12 for family in protocol["families"]))

    def test_operational_protocol_is_default_and_has_648_jobs(self):
        protocol = routing.validate_protocol(copy.deepcopy(self.operational_protocol))
        plan = self.plan(protocol)
        self.assertEqual(routing.DEFAULT_PROTOCOL.name, "routing-operational-v1.json")
        self.assertEqual(648, len(plan["jobs"]))
        self.assertEqual(3, protocol["replicatesPerFixture"])
        self.assertTrue(all(len(family["heldOutFixtureIds"]) == 6 for family in protocol["families"]))
        routing.validate_protocol_sources(
            protocol,
            routing.load_json(ROOT / "matrix.json"),
            routing.load_json(ROOT / "fixtures" / "catalog.json"),
        )

    def test_protocol_rejects_empty_fixture_catalog_and_extra_fields(self):
        protocol = self.small_protocol()
        protocol["families"][0]["heldOutFixtureIds"] = []
        with self.assertRaisesRegex(routing.ValidationError, "no held-out fixtures"):
            routing.validate_protocol(protocol)
        protocol = self.small_protocol()
        protocol["unregistered"] = True
        with self.assertRaisesRegex(routing.ValidationError, "must contain exactly"):
            routing.validate_protocol(protocol)

    def test_frozen_protocol_is_bound_to_matrix_and_catalog(self):
        matrix = routing.load_json(ROOT / "matrix.json")
        catalog = routing.load_json(ROOT / "fixtures" / "catalog.json")
        routing.validate_protocol_sources(self.frozen_protocol, matrix, catalog)
        changed = copy.deepcopy(matrix)
        changed["configurations"][0]["model"] = "different"
        with self.assertRaisesRegex(routing.ValidationError, "matrix.json"):
            routing.validate_protocol_sources(self.frozen_protocol, changed, catalog)
        missing = copy.deepcopy(catalog)
        missing["tasks"] = [
            task for task in missing["tasks"] if task["id"] != "mechanical-01"
        ]
        with self.assertRaisesRegex(routing.ValidationError, "mechanical-01"):
            routing.validate_protocol_sources(self.frozen_protocol, matrix, missing)

    def test_plan_is_deterministic_hmac_blinded_and_order_balanced(self):
        protocol = self.small_protocol()
        first = self.plan(protocol)
        second = self.plan(protocol)
        other_key = self.plan(protocol, b"fedcba9876543210")
        self.assertEqual(first, second)
        self.assertNotEqual(
            [job["runId"] for job in first["jobs"]],
            [job["runId"] for job in other_key["jobs"]],
        )
        family_id = protocol["families"][0]["id"]
        counts = {
            treatment["id"]: [0] * 6 for treatment in protocol["matrix"]
        }
        for job in first["jobs"]:
            if job["familyId"] == family_id:
                counts[job["treatmentId"]][job["orderPosition"]] += 1
        self.assertTrue(all(len(set(positions)) == 1 for positions in counts.values()))

    def test_plan_requires_complete_machine_preflights_and_rejects_drift(self):
        protocol = self.small_protocol()
        reports, runtime = self.preflights(protocol)
        with self.assertRaisesRegex(routing.ValidationError, "one preflight report per machine"):
            routing.make_plan(protocol, b"0123456789abcdef", [], runtime)
        changed = copy.deepcopy(reports[0])
        changed["capabilityDigest"] = "0" * 64
        with self.assertRaisesRegex(routing.ValidationError, "capability digest"):
            routing.make_plan(protocol, b"0123456789abcdef", [changed], runtime)

    def test_supported_decision_and_analysis_are_deterministic(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        results = self.results(protocol, plan, "supported")
        first = routing.analyze(protocol, plan, results)
        second = routing.analyze(protocol, plan, list(reversed(results)))
        self.assertEqual(first, second)
        self.assertTrue(all(item["decision"] == "SUPPORTED" for item in first["families"]))
        self.assertEqual(42, first["multiplicity"]["supportHypotheses"])
        self.assertEqual(42, first["multiplicity"]["contradictionHypotheses"])
        self.assertTrue(all(item["robustness"]["passed"] for item in first["families"]))
        self.assertTrue(all(len(item["robustness"]["machines"]) == 1 for item in first["families"]))
        self.assertTrue(all(len(item["robustness"]["ecosystems"]) == 4 for item in first["families"]))
        self.assertTrue(all(len(item["robustness"]["leaveOneFixtureOut"]) == 6 for item in first["families"]))
        self.assertEqual(
            protocol["familywiseAlpha"] / 42,
            first["multiplicity"]["simultaneousTailAlpha"],
        )
        routing.validate_analysis(first, protocol, plan)

    def test_centered_bootstrap_tests_the_null_boundary_not_raw_draws(self):
        draws = [0.51] * 95 + [1.0] * 5
        # Raw tail counting at the .50 boundary would return 1/(B+1), while
        # the null-centered upper tail retains the asymmetric uncertainty.
        support_p = routing.centered_bootstrap_p(draws, 0.75, 0.50, "greater")
        self.assertGreater(support_p, 0.05)
        with self.assertRaisesRegex(routing.ValidationError, "alternative"):
            routing.centered_bootstrap_p(draws, 0.80, 0.50, "two-sided")

    def test_holm_family_covers_quality_comparisons_and_decision_gates(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        analysis = routing.analyze(protocol, plan, self.results(protocol, plan, "supported"))
        raw_support = []
        adjusted_support = []
        for family in analysis["families"]:
            raw_support.append(family["absoluteQuality"]["rawSupportP"])
            adjusted_support.append(family["absoluteQuality"]["holmAdjustedSupportP"])
            raw_support.extend(item["rawNonInferiorityP"] for item in family["comparisons"])
            adjusted_support.extend(
                item["holmAdjustedNonInferiorityP"] for item in family["comparisons"]
            )
            raw_support.append(family["decisionGate"]["rawSupportP"])
            adjusted_support.append(family["decisionGate"]["holmAdjustedSupportP"])
            self.assertLessEqual(
                family["absoluteQuality"]["lower95"],
                family["absoluteQuality"]["nominalLower95"],
            )
            self.assertLessEqual(
                family["decisionGate"]["lower95"],
                family["decisionGate"]["nominalLower95"],
            )
        self.assertEqual(42, len(raw_support))
        self.assertEqual(routing.holm_adjust(raw_support), adjusted_support)

    def test_equal_quality_does_not_support_capability_routes(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        analysis = routing.analyze(protocol, plan, self.results(protocol, plan, "equal"))
        by_family = {item["familyId"]: item for item in analysis["families"]}
        mechanical = protocol["families"][0]
        self.assertEqual("SUPPORTED", by_family[mechanical["id"]]["decision"])
        for family in protocol["families"][1:]:
            self.assertEqual("CONTRADICTED", by_family[family["id"]]["decision"])
            self.assertIn("capability-gain-contradicted", by_family[family["id"]]["reasons"])

    def test_inconclusive_decision(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        analysis = routing.analyze(protocol, plan, self.results(protocol, plan, "inconclusive"))
        target = analysis["families"][0]
        self.assertEqual("INCONCLUSIVE", target["decision"])
        self.assertIn("absolute-quality-gate", target["reasons"])

    def test_contradicted_decision(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        analysis = routing.analyze(protocol, plan, self.results(protocol, plan, "contradicted"))
        self.assertTrue(all(item["decision"] == "CONTRADICTED" for item in analysis["families"]))
        self.assertTrue(all(
            "absolute-quality-contradicted" in item["reasons"]
            for item in analysis["families"]
        ))

    def test_incomplete_and_unresolved_cohorts_cannot_produce_decisions(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        results = self.results(protocol, plan, "supported")
        with self.assertRaisesRegex(routing.ValidationError, "complete resolved cohort"):
            routing.analyze(protocol, plan, results[:-1])
        results[0]["status"] = "INFRA_FAILURE"
        with self.assertRaisesRegex(routing.ValidationError, "complete resolved cohort"):
            routing.analyze(protocol, plan, results)

    def test_analysis_hash_tampering_is_rejected(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        analysis = routing.analyze(protocol, plan, self.results(protocol, plan, "supported"))
        analysis["planHash"] = "0" * 64
        with self.assertRaisesRegex(routing.ValidationError, "hashes do not match"):
            routing.validate_analysis(analysis, protocol, plan)

    def test_adjustment_and_decision_tampering_are_rejected(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        original = routing.analyze(protocol, plan, self.results(protocol, plan, "supported"))
        changed = copy.deepcopy(original)
        changed["families"][0]["absoluteQuality"]["holmAdjustedSupportP"] = 1.0
        with self.assertRaisesRegex(routing.ValidationError, "Holm adjustments"):
            routing.validate_analysis(changed, protocol, plan)
        changed = copy.deepcopy(original)
        changed["families"][0]["decision"] = "INCONCLUSIVE"
        with self.assertRaisesRegex(routing.ValidationError, "decision or reasons"):
            routing.validate_analysis(changed, protocol, plan)

    def test_robustness_is_a_required_supported_gate(self):
        protocol = self.small_protocol()
        plan = self.plan(protocol)
        analysis = routing.analyze(protocol, plan, self.results(protocol, plan, "supported"))
        target = analysis["families"][0]
        target["robustness"]["passed"] = False
        target["robustness"]["reasons"] = ["ecosystem-gain-instability"]
        decision, reasons = routing._classify_family_analysis(
            target, protocol["familywiseAlpha"]
        )
        self.assertEqual("INCONCLUSIVE", decision)
        self.assertIn("ecosystem-gain-instability", reasons)


if __name__ == "__main__":
    unittest.main()
