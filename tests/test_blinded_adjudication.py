import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import blinded_adjudication as adjudication  # noqa: E402
import construct_readiness  # noqa: E402
import harness  # noqa: E402


class BlindedAdjudicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads(
            (ROOT / "protocols/routing-operational-v1.json").read_text(encoding="utf-8")
        )
        cls.catalog = json.loads((ROOT / "fixtures/catalog.json").read_text(encoding="utf-8"))
        cls.assignment, cls.reveal = adjudication.prepare(
            cls.protocol, cls.catalog, b"0123456789abcdef0123456789abcdef"
        )

    def completed_rating(self, rater_id):
        rating = adjudication.rating_template(self.assignment, rater_id)
        reveal = {case["caseId"]: case for case in self.reveal["cases"]}
        for item in rating["ratings"]:
            item["accepted"] = reveal[item["caseId"]]["automatedAccepted"]
        return rating

    def test_packet_is_blinded_complete_and_deterministic(self):
        again, reveal = adjudication.prepare(
            self.protocol, self.catalog, b"0123456789abcdef0123456789abcdef"
        )
        self.assertEqual(self.assignment, again)
        self.assertEqual(self.reveal, reveal)
        self.assertEqual(36, len(self.assignment["cases"]))
        serialized = adjudication.canonical_json(self.assignment)
        self.assertNotIn("sourceType", serialized)
        self.assertNotIn("automatedAccepted", serialized)

    def test_two_complete_raters_produce_replayable_schema_v2_artifact(self):
        artifact = adjudication.aggregate(
            self.protocol, self.catalog, self.assignment, self.reveal,
            [self.completed_rating("rater-a"), self.completed_rating("rater-b")],
        )
        adjudication.validate_artifact(artifact, self.protocol, self.catalog)
        harness.validate_schema_instance(
            artifact,
            json.loads((ROOT / "schemas/blinded-adjudication.schema.json").read_text()),
        )
        self.assertEqual(3, len(artifact["families"]))
        self.assertTrue(all(item["sampleSize"] == 12 for item in artifact["families"]))
        self.assertTrue(all(item["unresolvedDisagreements"] == 0 for item in artifact["families"]))
        report = construct_readiness.build_report(
            self.protocol, self.catalog,
            calibration=json.loads(
                (ROOT / "runs/routing-docker-calibration-current.json").read_text()
            ),
            adjudication=artifact,
        )
        self.assertTrue(report["campaignEligible"])

    def test_disagreement_and_tampering_fail_closed(self):
        first, second = self.completed_rating("rater-a"), self.completed_rating("rater-b")
        second["ratings"][0]["accepted"] = not second["ratings"][0]["accepted"]
        artifact = adjudication.aggregate(
            self.protocol, self.catalog, self.assignment, self.reveal, [first, second]
        )
        affected = next(item for item in artifact["families"] if item["unresolvedDisagreements"])
        self.assertEqual(1, affected["unresolvedDisagreements"])
        changed = copy.deepcopy(artifact)
        changed["auditCases"][0]["resolved"] = True
        with self.assertRaisesRegex(adjudication.AdjudicationError, "does not reproduce"):
            adjudication.validate_artifact(changed, self.protocol, self.catalog)
        forged_reveal = copy.deepcopy(self.reveal)
        forged_reveal["cases"][0]["automatedAccepted"] = not forged_reveal["cases"][0]["automatedAccepted"]
        with self.assertRaisesRegex(adjudication.AdjudicationError, "contradicts"):
            adjudication.aggregate(
                self.protocol, self.catalog, self.assignment, forged_reveal, [first, second]
            )

    def test_duplicate_or_incomplete_raters_are_rejected(self):
        first = self.completed_rating("same-rater")
        with self.assertRaisesRegex(adjudication.AdjudicationError, "distinct"):
            adjudication.aggregate(
                self.protocol, self.catalog, self.assignment, self.reveal, [first, copy.deepcopy(first)]
            )
        incomplete = self.completed_rating("rater-b")
        incomplete["ratings"].pop()
        with self.assertRaisesRegex(adjudication.AdjudicationError, "incomplete"):
            adjudication.aggregate(
                self.protocol, self.catalog, self.assignment, self.reveal, [first, incomplete]
            )


if __name__ == "__main__":
    unittest.main()
