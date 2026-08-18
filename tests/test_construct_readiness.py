import copy
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import construct_readiness as readiness
import harness
import routing_campaign


ROOT = Path(__file__).resolve().parents[1]


class ConstructReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = readiness.load_json(ROOT / "protocols" / "routing-operational-v1.json")
        cls.catalog = readiness.load_json(ROOT / "fixtures" / "catalog.json")

    def build(self, **kwargs):
        # Criterion behavior is tested independently by routing_tasks. Avoid executing
        # sealed evaluators in these contract tests.
        with patch.object(readiness, "_criterion_coverage", return_value=(1, 1)):
            return readiness.build_report(self.protocol, self.catalog, **kwargs)

    def complete_calibration(self):
        cases = []
        for task in self.catalog["tasks"]:
            for label, outcome in (("reference", "PASS"), ("mutant", "FAIL")):
                cases.append({
                    "template": task["template"], "case": label,
                    "expected": outcome, "actual": outcome,
                })
            cases.append({
                "template": task["template"], "case": "equivalent-positive",
                "expected": "PASS", "actual": "PASS",
            })
            if task["adapter"] == readiness.ARTIFACT_RUBRIC:
                cases.append({
                    "template": task["template"], "case": "schema-extra-mutant",
                    "expected": "FAIL", "actual": "FAIL",
                })
                root = readiness.routing_tasks.template_root(task)
                spec = readiness.routing_tasks.load_template(task)
                reference = json.loads(
                    (root / "reference" / spec["mutable"][0]).read_text(encoding="utf-8")
                )
                for criterion_id, _ in readiness.routing_tasks.artifact_criterion_mutants(
                    task, spec, reference
                ):
                    cases.append({
                        "template": task["template"], "case": "criterion-mutant",
                        "criterionId": criterion_id,
                        "expected": "FAIL", "actual": "FAIL",
                    })
            elif task["adapter"] == "json-semantic-diff-v1":
                spec = readiness.routing_tasks.load_template(task)
                for criterion_id in readiness.routing_tasks.json_state_criterion_ids(spec):
                    cases.append({
                        "template": task["template"], "case": "criterion-mutant",
                        "criterionId": criterion_id,
                        "expected": "FAIL", "actual": "FAIL",
                    })
        return {
            "schemaVersion": 1, "recordKind": "routing-calibration", "backend": "docker",
            "catalogHash": readiness.value_hash(self.catalog),
            "evaluatorImage": readiness.routing_tasks.ROUTING_EVALUATOR_IMAGE,
            "evaluatorImageId": "sha256:" + "a" * 64, "passed": True, "cases": cases,
        }

    def test_current_report_is_machine_verified_and_campaign_eligible(self):
        report = readiness.load_json(ROOT / "runs" / "construct-readiness-current.json")
        readiness.validate_report(report, self.protocol, self.catalog)
        harness.validate_schema_instance(
            report, readiness.load_json(ROOT / "schemas" / "construct-readiness.schema.json")
        )
        self.assertTrue(report["campaignEligible"])
        self.assertTrue(all(not item["reasons"] for item in report["families"]))

    def test_scoped_strong_family_can_pass_when_evidence_is_complete(self):
        report = self.build(calibration=self.complete_calibration())
        families = {item["catalogFamilyId"]: item for item in report["families"]}
        self.assertTrue(families["mechanical"]["eligible"])
        self.assertTrue(families["isolated-implementation"]["eligible"])
        self.assertIn("sealed behavioral", families["isolated-implementation"]["scopedClaim"])
        self.assertTrue(families["coordination-integration"]["eligible"])
        self.assertTrue(families["high-risk-change"]["eligible"])

    def test_report_hash_and_eligibility_tampering_are_rejected(self):
        report = self.build()
        changed = copy.deepcopy(report)
        changed["families"][0]["eligible"] = True
        unsigned = {key: value for key, value in changed.items() if key != "reportHash"}
        changed["reportHash"] = readiness.value_hash(unsigned)
        with self.assertRaisesRegex(readiness.ReadinessError, "does not reproduce"):
            readiness.validate_report(changed, self.protocol, self.catalog)
        changed = copy.deepcopy(report)
        changed["reportHash"] = "0" * 64
        with self.assertRaisesRegex(readiness.ReadinessError, "hash mismatch"):
            readiness.validate_report(changed, self.protocol, self.catalog)

    def test_paid_campaign_authorization_accepts_current_machine_verified_instrument(self):
        report = readiness.load_json(ROOT / "runs" / "construct-readiness-current.json")
        readiness.assert_campaign_ready(report, self.protocol, self.catalog)

    def test_routing_plan_cli_requires_construct_readiness_report(self):
        with patch("sys.argv", [
            "routing_campaign.py", "plan", "--id-key-file", "key",
            "--preflight", "preflight.json", "--output", "plan.json",
        ]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                routing_campaign.parse_args()
        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
