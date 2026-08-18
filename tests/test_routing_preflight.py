import copy
import json
import unittest
from pathlib import Path

import routing_campaign
import routing_preflight
import routing_runner


ROOT = Path(__file__).resolve().parents[1]


class RoutingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.protocol = routing_campaign.load_json(ROOT / "protocols" / "routing-v1.json")
        self.runtime = routing_runner.load_runtime_manifest()
        self.models = []
        for model in sorted({item["model"] for item in self.protocol["matrix"]}):
            self.models.append({
                "id": model, "model": model,
                "supportedReasoningEfforts": [
                    {"reasoningEffort": effort, "description": effort}
                    for effort in ("low", "medium", "high")
                ],
                "serviceTiers": [{"id": "priority", "name": "Fast", "description": "Fast"}],
                "additionalSpeedTiers": ["fast"],
            })

    def test_catalog_covers_every_frozen_treatment(self):
        checked = routing_preflight.validate_catalog(self.protocol, self.runtime, self.models)
        self.assertEqual([item["id"] for item in self.protocol["matrix"]], [item["id"] for item in checked])
        self.assertTrue(all(item["supported"] for item in checked))

    def test_missing_effort_or_priority_is_rejected(self):
        changed = copy.deepcopy(self.models)
        changed[0]["serviceTiers"] = []
        changed[0]["additionalSpeedTiers"] = []
        with self.assertRaises(routing_preflight.PreflightError):
            routing_preflight.validate_catalog(self.protocol, self.runtime, changed)

    def test_report_reverse_binds_machine_protocol_and_capabilities(self):
        image = {
            "tag": "test", "id": "sha256:" + "a" * 64, "repoDigests": [],
            "os": "linux", "architecture": "arm64", "specHash": "b" * 64,
        }
        machine = self.protocol["machines"][0]
        report = routing_preflight.make_report(
            self.protocol, self.runtime, self.models, image,
            self.runtime["codexVersion"], machine,
        )
        self.assertEqual(machine, report["machineId"])
        routing_preflight.validate_report(
            report, self.protocol, self.runtime, expected_machine_id=machine
        )
        changed = copy.deepcopy(report)
        changed["protocolHash"] = "0" * 64
        with self.assertRaisesRegex(routing_preflight.PreflightError, "reverse-bind"):
            routing_preflight.validate_report(changed, self.protocol, self.runtime)


if __name__ == "__main__":
    unittest.main()
