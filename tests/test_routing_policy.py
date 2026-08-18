import copy
import json
import re
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
        expected = {item["id"]: (item["model"], item["reasoningEffort"])
                    for item in self.matrix}
        actual = {
            item["selectedConfigurationId"]: (item["model"], item["reasoningEffort"])
            for item in self.policy["defaults"]
        }
        self.assertEqual(expected, actual)
        self.assertEqual(routing_policy.EXPECTED_CONFIGURATION_COST_ORDER,
                         self.policy["configurationCostOrder"])
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

    def test_matrix_membership_rejects_unknown_or_changed_selected_configuration(self):
        policy = copy.deepcopy(self.policy)
        policy["defaults"][0]["model"] = "different-model"
        with self.assertRaisesRegex(ValueError, "must match"):
            self.validate(policy)

        policy = copy.deepcopy(self.policy)
        policy["defaults"][0]["selectedConfigurationId"] = "not-a-configuration"
        with self.assertRaisesRegex(ValueError, "unknown configuration"):
            self.validate(policy)

    def test_duplicate_task_ids_and_invalid_availability_fallbacks_are_rejected(self):
        policy = copy.deepcopy(self.policy)
        policy["defaults"][1]["id"] = policy["defaults"][0]["id"]
        with self.assertRaises(ValueError):
            self.validate(policy)

        policy = copy.deepcopy(self.policy)
        policy["defaults"][0]["availabilityFallbackConfigurationIds"] = ["not-a-configuration"]
        with self.assertRaisesRegex(ValueError, "unknown availability fallback"):
            self.validate(policy)

        policy = copy.deepcopy(self.policy)
        policy["defaults"][1]["precedence"] = policy["defaults"][0]["precedence"]
        with self.assertRaisesRegex(ValueError, "precedence"):
            self.validate(policy)

    def test_cost_order_and_availability_fallbacks_are_strict(self):
        policy = copy.deepcopy(self.policy)
        policy["configurationCostOrder"] = list(reversed(policy["configurationCostOrder"]))
        with self.assertRaisesRegex(ValueError, "configurationCostOrder"):
            self.validate(policy)

        policy = copy.deepcopy(self.policy)
        policy["defaults"][1]["availabilityFallbackConfigurationIds"] = ["luna-low"]
        with self.assertRaisesRegex(ValueError, "costlier"):
            self.validate(policy)

        policy = copy.deepcopy(self.policy)
        policy["defaults"][0]["availabilityFallbackConfigurationIds"] = ["sol-high", "luna-medium"]
        with self.assertRaisesRegex(ValueError, "increasing cost"):
            self.validate(policy)

    def test_resolver_classifies_task_then_uses_its_selected_configuration(self):
        selected = routing_policy.resolve_route(
            self.policy, ["mechanical-repository-work", "coordination-integration", "isolated-implementation-debugging"]
        )
        self.assertEqual("coordination-integration", selected["id"])
        self.assertEqual("sol-medium", selected["resolvedConfigurationId"])

        policy = copy.deepcopy(self.policy)
        by_id = {item["id"]: item for item in policy["defaults"]}
        by_id["mechanical-repository-work"]["safetyRank"] = 100
        by_id["bounded-mapping-and-patch"]["safetyRank"] = 100
        by_id["mechanical-repository-work"]["specificity"] = 80
        by_id["bounded-mapping-and-patch"]["specificity"] = 70
        selected = routing_policy.resolve_route(policy, ["bounded-mapping-and-patch", "mechanical-repository-work"])
        self.assertEqual("mechanical-repository-work", selected["id"])

        by_id["bounded-mapping-and-patch"]["specificity"] = 80
        by_id["mechanical-repository-work"]["precedence"] = 30
        by_id["bounded-mapping-and-patch"]["precedence"] = 20
        selected = routing_policy.resolve_route(policy, ["mechanical-repository-work", "bounded-mapping-and-patch"])
        self.assertEqual("bounded-mapping-and-patch", selected["id"])

    def test_uncertain_high_risk_routes_to_sol_high(self):
        selected = routing_policy.resolve_route(
            self.policy, ["mechanical-repository-work"], uncertain_high_risk=True
        )
        self.assertEqual("ambiguous-cross-cutting-high-risk", selected["id"])

    def test_fallback_order_and_exhaustion_are_deterministic(self):
        selected = routing_policy.resolve_route(
            self.policy, ["mechanical-repository-work"], available_configuration_ids={"sol-medium", "sol-high"}
        )
        self.assertEqual("sol-medium", selected["resolvedConfigurationId"])
        selected = routing_policy.resolve_route(
            self.policy, ["mechanical-repository-work"], available_configuration_ids={"sol-high"}
        )
        self.assertEqual("sol-high", selected["resolvedConfigurationId"])
        self.assertIsNone(routing_policy.resolve_route(
            self.policy, ["ambiguous-cross-cutting-high-risk"], available_configuration_ids=set()
        ))

    def test_multiple_task_routes_may_select_the_same_configuration(self):
        policy = copy.deepcopy(self.policy)
        policy["defaults"][1]["selectedConfigurationId"] = "luna-low"
        policy["defaults"][1]["model"] = "gpt-5.6-luna"
        policy["defaults"][1]["reasoningEffort"] = "low"
        policy["defaults"][1]["availabilityFallbackConfigurationIds"] = [
            "luna-medium", "luna-high", "terra-medium", "sol-medium", "sol-high"
        ]
        self.validate(policy)
        selected = routing_policy.resolve_route(policy, ["bounded-mapping-and-patch"])
        self.assertEqual("bounded-mapping-and-patch", selected["id"])
        self.assertEqual("luna-low", selected["resolvedConfigurationId"])

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
                    "cheapestSufficientConfigurationId": "luna-low",
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

            analysis["families"][0].pop("cheapestSufficientConfigurationId")
            (evidence_path / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
            with mock.patch.object(
                routing_policy.routing_evidence, "verify_bundle", return_value=bundle
            ), mock.patch.object(
                routing_policy.construct_readiness, "assert_families_ready"
            ), self.assertRaisesRegex(ValueError, "cheapest sufficient"):
                self.validate(
                    policy, evidence_paths={"campaign-1": evidence_path},
                    construct_readiness_path=evidence_path / "readiness.json",
                )
            analysis["families"][0]["cheapestSufficientConfigurationId"] = "luna-low"
            (evidence_path / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")

            policy["defaults"][0]["evidenceRefs"][0]["estimand"] = "coordinator"
            with self.assertRaisesRegex(ValueError, "estimand"):
                self.validate(policy, evidence_paths={"campaign-1": evidence_path})

            policy["defaults"][0]["evidenceRefs"][0]["estimand"] = "worker"
            policy["evidenceBundles"][0]["canonicalSha256"] = "f" * 64
            with mock.patch.object(
                routing_policy.routing_evidence, "verify_bundle", return_value=bundle
            ), self.assertRaisesRegex(ValueError, "hash mismatch"):
                self.validate(policy, evidence_paths={"campaign-1": evidence_path})

    def test_sequential_evidence_promotes_only_the_replayed_cheapest_sufficient_route(self):
        policy = copy.deepcopy(self.policy)
        route = policy["defaults"][0]
        route["claimStrength"] = "evidence-backed"
        policy["status"] = "mixed"
        route["evidenceRefs"] = [{
            "bundleId": "sequential-1", "estimand": "worker",
            "taskFamily": route["taskClass"], "analysisId": "routing-v1",
            "configurationId": route["selectedConfigurationId"],
            "decision": "ACCEPT",
        }]
        with tempfile.TemporaryDirectory() as temporary:
            evidence_path = Path(temporary) / "bundle"
            evidence_path.mkdir()
            analysis = {
                "schemaVersion": 1, "recordKind": "routing-sequential-analysis",
                "complete": True,
                "families": [{
                    "familyId": route["taskClass"],
                    "selectedTreatmentId": route["selectedConfigurationId"],
                    "cheapestSufficientConfigurationId": route["selectedConfigurationId"],
                    "decision": "ACCEPT",
                }],
            }
            (evidence_path / "analysis.json").write_text(json.dumps(analysis))
            bundle = {
                "schemaVersion": 1,
                "recordKind": routing_policy.routing_sequential_evidence.BUNDLE_KIND,
                "estimand": "worker", "bundleId": "sequential-1",
                "protocolId": "routing-v1", "protocolHash": "1" * 64,
                "planHash": "2" * 64,
                "decisions": [{
                    "familyId": route["taskClass"],
                    "selectedTreatmentId": route["selectedConfigurationId"],
                    "decision": "ACCEPT",
                }],
                "artifacts": {"analysis": "analysis.json"},
            }
            (evidence_path / "bundle.json").write_text(json.dumps(bundle))
            policy["evidenceBundles"] = [{
                "id": "sequential-1", "estimand": "worker", "schemaVersion": 1,
                "canonicalSha256": routing_policy.canonical_sha256(bundle),
                "protocolHash": "1" * 64, "planHash": "2" * 64,
                "taskFamilies": [route["taskClass"]],
            }]
            readiness = evidence_path / "readiness.json"
            readiness.write_text("{}")
            with mock.patch.object(
                routing_policy.routing_sequential_evidence, "verify_bundle", return_value=bundle
            ), mock.patch.object(
                routing_policy.construct_readiness, "assert_families_ready"
            ):
                self.validate(
                    policy, evidence_paths={"sequential-1": evidence_path},
                    construct_readiness_path=readiness,
                )

            analysis["families"][0]["cheapestSufficientConfigurationId"] = "luna-medium"
            (evidence_path / "analysis.json").write_text(json.dumps(analysis))
            with mock.patch.object(
                routing_policy.routing_sequential_evidence, "verify_bundle", return_value=bundle
            ), mock.patch.object(
                routing_policy.construct_readiness, "assert_families_ready"
            ), self.assertRaisesRegex(ValueError, "cheapest sufficient"):
                self.validate(
                    policy, evidence_paths={"sequential-1": evidence_path},
                    construct_readiness_path=readiness,
                )

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
        template = routing_policy.DEFAULT_TEMPLATE.read_text(encoding="utf-8")
        self.assertEqual(current, routing_policy.render_skill(template, self.policy))
        block = routing_policy.generated_block(self.policy)
        self.assertIn(routing_policy.FAST_MODE_TEXT, block)
        self.assertNotIn("<!--", current)
        self.assertNotIn(routing_policy.ROUTING_PLACEHOLDER, current)
        self.assertNotIn("](", current)
        self.assertIsNone(re.search(
            r"\b[\w./-]+\.(?:json|md|ya?ml|toml|py)\b", current,
            flags=re.IGNORECASE,
        ))
        for route in self.policy["defaults"]:
            self.assertIn(f"`{route['id']}`", block)
            self.assertIn(f"`{route['selectedConfigurationId']}`", block)
            self.assertIn(f"`{route['claimStrength']}`", block)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            routing_policy.render_skill(
                template + "\n" + routing_policy.ROUTING_PLACEHOLDER,
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
            skill.write_text(
                current.replace("Status: `provisional`", "Status: `drifted`"),
                encoding="utf-8",
            )
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
