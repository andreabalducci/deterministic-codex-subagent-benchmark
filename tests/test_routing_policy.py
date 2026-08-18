import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_policy  # noqa: E402


class RoutingPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = routing_policy.load_json(routing_policy.DEFAULT_ARTIFACT)
        self.matrix = routing_policy.load_matrix()
        self.schema = routing_policy.load_schema()

    def validate(self, policy=None, matrix=None, evidence_paths=None, construct_readiness_path=None):
        routing_policy.validate_policy(
            self.policy if policy is None else policy,
            self.matrix if matrix is None else matrix,
            schema=self.schema,
            evidence_paths=evidence_paths,
            construct_readiness_path=construct_readiness_path,
        )

    def test_checked_in_policy_is_strict_and_matches_matrix(self):
        self.validate()
        expected = {
            item["id"]: (item["model"], item["reasoningEffort"])
            for item in self.matrix
        }
        actual = {
            item["id"]: (item["model"], item["reasoningEffort"])
            for item in self.policy["defaults"]
        }
        self.assertEqual(expected, actual)
        self.assertEqual("provisional", self.policy["status"])
        self.assertEqual({"hypothesis"}, {
            item["claimStrength"] for item in self.policy["defaults"]
        })

    def test_schema_rejects_additional_properties_at_every_level(self):
        for mutate in (
            lambda value: value.update({"extra": True}),
            lambda value: value["fastMode"].update({"extra": True}),
            lambda value: value["defaults"][0].update({"extra": True}),
        ):
            policy = copy.deepcopy(self.policy)
            mutate(policy)
            with self.assertRaisesRegex(ValueError, "additional property"):
                self.validate(policy)

    def test_matrix_membership_rejects_missing_or_changed_treatment(self):
        policy = copy.deepcopy(self.policy)
        policy["defaults"][0]["model"] = "different-model"
        with self.assertRaisesRegex(ValueError, "exactly match"):
            self.validate(policy)

        with self.assertRaisesRegex(ValueError, "exactly match"):
            self.validate(matrix=self.matrix[:-1])

    def test_duplicate_ids_and_unknown_fallbacks_are_rejected(self):
        policy = copy.deepcopy(self.policy)
        policy["defaults"][1]["id"] = policy["defaults"][0]["id"]
        with self.assertRaises(ValueError):
            self.validate(policy)

        policy = copy.deepcopy(self.policy)
        policy["defaults"][0]["fallbackRouteIds"] = ["not-a-route"]
        with self.assertRaisesRegex(ValueError, "Unknown fallback"):
            self.validate(policy)

        policy = copy.deepcopy(self.policy)
        policy["defaults"][1]["precedence"] = policy["defaults"][0]["precedence"]
        with self.assertRaisesRegex(ValueError, "precedence"):
            self.validate(policy)

    def test_fallback_cycles_are_rejected(self):
        policy = copy.deepcopy(self.policy)
        policy["defaults"][-1]["fallbackRouteIds"] = ["luna-low"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.validate(policy)

    def test_resolver_uses_safety_specificity_then_precedence(self):
        selected = routing_policy.resolve_route(
            self.policy, ["luna-low", "sol-medium", "luna-high"]
        )
        self.assertEqual("sol-medium", selected["id"])

        policy = copy.deepcopy(self.policy)
        by_id = {item["id"]: item for item in policy["defaults"]}
        by_id["luna-low"]["safetyRank"] = 100
        by_id["luna-medium"]["safetyRank"] = 100
        by_id["luna-low"]["specificity"] = 80
        by_id["luna-medium"]["specificity"] = 70
        selected = routing_policy.resolve_route(policy, ["luna-medium", "luna-low"])
        self.assertEqual("luna-low", selected["id"])

        by_id["luna-medium"]["specificity"] = 80
        by_id["luna-low"]["precedence"] = 30
        by_id["luna-medium"]["precedence"] = 20
        selected = routing_policy.resolve_route(policy, ["luna-low", "luna-medium"])
        self.assertEqual("luna-medium", selected["id"])

    def test_uncertain_high_risk_routes_to_sol_high(self):
        selected = routing_policy.resolve_route(
            self.policy, ["luna-low"], uncertain_high_risk=True
        )
        self.assertEqual("sol-high", selected["id"])

    def test_fallback_order_and_exhaustion_are_deterministic(self):
        selected = routing_policy.resolve_route(
            self.policy, ["luna-low"], available_route_ids={"sol-medium", "sol-high"}
        )
        self.assertEqual("sol-medium", selected["id"])
        selected = routing_policy.resolve_route(
            self.policy, ["luna-low"], available_route_ids={"sol-high"}
        )
        self.assertEqual("sol-high", selected["id"])
        self.assertIsNone(routing_policy.resolve_route(
            self.policy, ["sol-high"], available_route_ids=set()
        ))

    def test_evidence_backed_claim_requires_references_and_resolution(self):
        policy = copy.deepcopy(self.policy)
        policy["defaults"][0]["claimStrength"] = "evidence-backed"
        policy["status"] = "mixed"
        with self.assertRaisesRegex(ValueError, "no evidence references"):
            self.validate(policy)

        policy["defaults"][0]["evidenceRefs"] = [{
            "bundleId": "campaign-1",
            "estimand": "worker",
            "taskFamily": "mechanical-repository-work",
            "analysisId": "routing-v1",
            "configurationId": "luna-low",
            "comparisonConfigurationIds": ["luna-medium"],
            "metric": "generation-duration-gain-fraction",
            "estimate": 0.2,
            "interval95": [0.15, 0.25],
            "decision": "SUPPORTED",
        }]
        policy["evidenceBundles"] = [{
            "id": "campaign-1",
            "estimand": "worker",
            "schemaVersion": 1,
            "canonicalSha256": "0" * 64,
            "protocolHash": "1" * 64,
            "planHash": "2" * 64,
            "taskFamilies": ["mechanical-repository-work"],
        }]
        with self.assertRaisesRegex(ValueError, "cannot resolve"):
            self.validate(policy)

        with tempfile.TemporaryDirectory() as temporary:
            evidence_path = Path(temporary) / "bundle"
            evidence_path.mkdir()
            analysis = {
                "schemaVersion": 2, "recordKind": "routing-analysis",
                "complete": True, "protocolId": "routing-v1",
                "families": [{
                    "familyId": "mechanical-repository-work",
                    "candidateId": "luna-low", "decision": "SUPPORTED",
                    "comparisons": [{"comparatorId": "luna-medium"}],
                    "decisionGate": {
                        "metric": "generation-duration-gain-fraction",
                        "gain": 0.2, "lower95": 0.15, "upper95": 0.25,
                    },
                }],
            }
            (evidence_path / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
            bundle = {
                "schemaVersion": 1, "estimand": "worker", "bundleId": "campaign-1",
                "protocolHash": "1" * 64, "planHash": "2" * 64,
                "decisions": [{
                    "familyId": "mechanical-repository-work",
                    "candidateId": "luna-low", "decision": "SUPPORTED",
                }],
                "artifacts": {"analysis": "analysis.json"},
            }
            policy["evidenceBundles"][0]["canonicalSha256"] = (
                routing_policy.canonical_sha256(bundle)
            )
            with mock.patch.object(
                routing_policy.routing_evidence, "verify_bundle", return_value=bundle
            ), self.assertRaisesRegex(ValueError, "construct readiness"):
                self.validate(policy, evidence_paths={"campaign-1": evidence_path})

            (evidence_path / "readiness.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(
                routing_policy.routing_evidence, "verify_bundle", return_value=bundle
            ), mock.patch.object(
                routing_policy.construct_readiness, "assert_families_ready"
            ) as readiness_check:
                self.validate(
                    policy, evidence_paths={"campaign-1": evidence_path},
                    construct_readiness_path=evidence_path / "readiness.json",
                )
                readiness_check.assert_called_once()

            policy["defaults"][0]["evidenceRefs"][0]["estimand"] = "coordinator"
            with self.assertRaisesRegex(ValueError, "estimand"):
                self.validate(policy, evidence_paths={"campaign-1": evidence_path})

            policy["defaults"][0]["evidenceRefs"][0]["estimand"] = "worker"
            policy["evidenceBundles"][0]["canonicalSha256"] = "f" * 64
            with mock.patch.object(
                routing_policy.routing_evidence, "verify_bundle", return_value=bundle
            ), self.assertRaisesRegex(ValueError, "hash mismatch"):
                self.validate(policy, evidence_paths={"campaign-1": evidence_path})

    def test_fast_mode_invariants_are_schema_enforced(self):
        expected = {
            "scope": "session",
            "spawnAgentParameter": False,
            "requiredForLuna": False,
            "inferFromModelOrEffort": False,
        }
        self.assertEqual(expected, self.policy["fastMode"])
        for key, invalid in (
            ("scope", "worker"),
            ("spawnAgentParameter", True),
            ("requiredForLuna", True),
            ("inferFromModelOrEffort", True),
        ):
            policy = copy.deepcopy(self.policy)
            policy["fastMode"][key] = invalid
            with self.assertRaises(ValueError):
                self.validate(policy)

    def test_canonical_hash_is_key_order_independent(self):
        reordered = json.loads(json.dumps(self.policy, sort_keys=True))
        self.assertEqual(
            routing_policy.canonical_sha256(self.policy),
            routing_policy.canonical_sha256(reordered),
        )

    def test_renderer_is_synchronized_and_preserves_other_skill_prose(self):
        current = routing_policy.DEFAULT_SKILL.read_text(encoding="utf-8")
        self.assertEqual(current, routing_policy.render_skill(current, self.policy))
        block = routing_policy.generated_block(self.policy)
        self.assertIn(routing_policy.canonical_sha256(self.policy), block)
        self.assertIn(routing_policy.FAST_MODE_TEXT, block)
        self.assertIn("Give every agent distinct ownership", current)
        self.assertIn("Never delegate merely to appear busy", current)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            routing_policy.render_skill(
                current + "\n" + routing_policy.BEGIN_MARKER,
                self.policy,
            )

    def test_check_command_detects_generated_block_drift(self):
        successful = subprocess.run(
            [sys.executable, str(ROOT / "routing_policy.py"), "--check"],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, successful.returncode, successful.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            skill = Path(temporary) / "SKILL.md"
            current = routing_policy.DEFAULT_SKILL.read_text(encoding="utf-8")
            skill.write_text(current.replace("working defaults", "drifted defaults"), encoding="utf-8")
            failed = subprocess.run(
                [
                    sys.executable, str(ROOT / "routing_policy.py"), "--check",
                    "--skill", str(skill),
                ],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(1, failed.returncode)
        self.assertIn("out of date", failed.stderr)


if __name__ == "__main__":
    unittest.main()
