import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_quality as quality  # noqa: E402
import routing_campaign as campaign  # noqa: E402
import routing_preflight  # noqa: E402
import routing_runner  # noqa: E402
import harness  # noqa: E402


class QualityRoutingTests(unittest.TestCase):
    def setUp(self):
        self.family = {
            "id": "bounded-mapping-and-patch",
            "heldOutFixtureIds": ["dotnet-01", "spa-01"],
            "heldOutFixtureEcosystems": ["dotnet", "react-typescript"],
            "absoluteQualityFloor": 0.75,
        }
        self.matrix = [
            {"id": "luna-low", "model": "gpt-6-luna", "reasoningEffort": "low"},
            {"id": "luna-medium", "model": "gpt-6-luna", "reasoningEffort": "medium"},
            {"id": "sol-medium", "model": "gpt-6-sol", "reasoningEffort": "medium"},
        ]
        self.rates = {
            "gpt-6-luna": {"inputPerMillion": 0.1, "outputPerMillion": 0.5},
            "gpt-6-sol": {"inputPerMillion": 2, "outputPerMillion": 10},
        }

    def results(self):
        rows = []
        for treatment in self.matrix:
            for fixture in self.family["heldOutFixtureIds"]:
                for replicate in range(2):
                    rows.append({
                        "treatmentId": treatment["id"], "fixtureId": fixture,
                        "replicate": replicate, "status": "PASS",
                        "usage": {"inputTokens": 1000, "outputTokens": 100},
                        "generationDurationSeconds": {
                            "luna-low": 3.0, "luna-medium": 2.0, "sol-medium": 1.0,
                        }[treatment["id"]],
                    })
        return rows

    def test_verified_quality_outweighs_cost_and_latency(self):
        rows = self.results()
        rows[0]["status"] = "CANDIDATE_FAILURE"
        rows[4]["status"] = "CANDIDATE_FAILURE"
        report = quality._scope(self.family, None, self.matrix, rows, self.rates)
        self.assertEqual("sol-medium", report["selectedTreatmentId"])
        self.assertEqual(["sol-medium"], report["qualityTieIds"])
        self.assertEqual(4, report["treatments"][2]["verifiedSuccesses"])
        self.assertEqual(3, report["treatments"][0]["verifiedSuccesses"])

    def test_cost_then_time_break_exact_quality_ties(self):
        report = quality._scope(self.family, None, self.matrix, self.results(), self.rates)
        self.assertEqual("luna-medium", report["selectedTreatmentId"])
        self.assertEqual(["luna-low", "luna-medium", "sol-medium"], report["qualityTieIds"])
        self.assertEqual(2.0, report["treatments"][1]["generationP50Seconds"])
        self.assertLess(
            report["treatments"][1]["uncachedStandardCostProxyUsd"],
            report["treatments"][2]["uncachedStandardCostProxyUsd"],
        )

    def test_missing_cost_blocks_a_quality_tie(self):
        rows = self.results()
        rows[0]["usage"]["inputTokens"] = None
        report = quality._scope(self.family, None, self.matrix, rows, self.rates)
        self.assertEqual("COST_DATA_MISSING", report["selectionStatus"])
        self.assertIsNone(report["selectedTreatmentId"])

    def test_no_selection_when_every_treatment_misses_quality_floor(self):
        rows = self.results()
        for row in rows:
            if row["replicate"] == 0:
                row["status"] = "CANDIDATE_FAILURE"
        report = quality._scope(self.family, "dotnet", self.matrix, rows, self.rates)
        self.assertEqual("BELOW_QUALITY_FLOOR", report["selectionStatus"])
        self.assertIsNone(report["selectedTreatmentId"])
        self.assertEqual(["dotnet-01"], report["fixtureIds"])

    def test_pricing_requires_official_complete_model_snapshot(self):
        pricing = {
            "asOf": "2026-09-22", "currency": "USD", "basis": "uncached",
            "models": {
                model: {**rates, "source": f"https://developers.openai.com/api/docs/models/{model}"}
                for model, rates in self.rates.items()
            },
        }
        self.assertEqual(pricing["models"], quality._pricing_rates(pricing, set(self.rates)))
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            quality._pricing_rates(pricing, {"gpt-6-luna"})
        pricing["models"]["gpt-6-sol"]["source"] = "https://example.com"
        with self.assertRaisesRegex(ValueError, "official OpenAI"):
            quality._pricing_rates(pricing, set(self.rates))


class QualityReportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = campaign.load_json(quality.DEFAULT_PROTOCOL)
        cls.pricing = campaign.load_json(quality.DEFAULT_PRICING)
        runtime = routing_runner.load_runtime_manifest()
        models = [{
            "id": model, "model": model,
            "supportedReasoningEfforts": [
                {"reasoningEffort": effort} for effort in ("low", "medium", "high")
            ],
            "serviceTiers": [{"id": "priority"}], "additionalSpeedTiers": ["fast"],
        } for model in sorted({item["model"] for item in cls.protocol["matrix"]})]
        image = {
            "tag": "test", "id": "sha256:" + "a" * 64, "repoDigests": [],
            "os": "linux", "architecture": "amd64", "specHash": "b" * 64,
        }
        preflight = routing_preflight.make_report(
            cls.protocol, runtime, models, image, runtime["codexVersion"], "machine-a"
        )
        cls.plan = campaign.make_plan(cls.protocol, b"0123456789abcdef", [preflight], runtime)

    def results(self):
        binding = self.plan["preflightBindings"][0]
        rows = []
        for job in self.plan["jobs"]:
            row = {key: job[key] for key in (
                "runId", "familyId", "fixtureId", "replicate", "machineId",
                "orderPosition", "treatmentId", "model", "reasoningEffort",
            )}
            row.update({
                "schemaVersion": 2, "recordKind": "routing-result",
                "protocolHash": campaign.value_hash(self.protocol),
                "planHash": campaign.value_hash(self.plan),
                "runtimeManifestHash": self.protocol["runtimeManifestHash"],
                "preflightReportHash": binding["reportHash"],
                "capabilityDigest": binding["capabilityDigest"],
                "status": "PASS", "failureKind": None,
                "fixtureManifestHash": "a" * 64, "promptHash": "b" * 64,
                "candidateHash": "c" * 64, "transcriptHash": "d" * 64,
                "generationDurationSeconds": float(
                    self.protocol["selection"]["costOrder"].index(job["treatmentId"]) + 1
                ),
                "evaluationDurationSeconds": 0.5, "totalDurationSeconds": 2.5,
                "provenance": {},
                "usage": {
                    "inputTokens": 1000, "outputTokens": 100,
                    "cachedInputTokens": None, "reasoningOutputTokens": 50,
                    "cacheWriteInputTokens": None, "totalTokens": 1100,
                },
                "evaluation": {
                    "backend": "native", "evaluatorProfile": None,
                    "evaluatorImage": None, "reportHash": "e" * 64,
                },
                "generation": {
                    "backend": "docker", "isolation": "container-strong",
                    "generatorImage": None, "codexVersion": "test",
                    "attemptHash": "f" * 64, "requestedModel": job["model"],
                    "requestedReasoningEffort": job["reasoningEffort"],
                    "requestedServiceTier": "priority", "fastMode": True,
                    "multiAgent": False, "observedModel": None,
                    "serviceTier": None, "runtimeVerification": "cli-request-and-success",
                },
            })
            rows.append(row)
        return rows

    def test_complete_cohort_ranks_each_family_and_ecosystem(self):
        report = quality.summarize(self.protocol, self.plan, self.results(), self.pricing)
        self.assertEqual(900, len(self.plan["jobs"]))
        self.assertEqual(18, len(report["rows"]))
        self.assertTrue(all(row["selectedTreatmentId"] == "luna-low" for row in report["rows"]))
        self.assertEqual({"all", "dotnet", "react-typescript"}, {
            row["ecosystem"] for row in report["rows"]
        })
        self.assertFalse(report["policyPromotionAllowed"])

    def test_quality_protocol_schema_requires_complete_cohort_decision(self):
        schema = campaign.load_json(ROOT / "schemas" / "routing-protocol.schema.json")
        harness.validate_schema_instance(self.protocol, schema, root_schema=schema)
        changed = {**self.protocol, "selection": {
            **self.protocol["selection"], "stageDecision": "accept-or-escalate"
        }}
        with self.assertRaises(ValueError):
            campaign.validate_protocol(changed)

    def test_incomplete_cohort_cannot_select_a_model(self):
        with self.assertRaisesRegex(ValueError, "complete, resolved"):
            quality.summarize(self.protocol, self.plan, self.results()[:-1], self.pricing)


if __name__ == "__main__":
    unittest.main()
