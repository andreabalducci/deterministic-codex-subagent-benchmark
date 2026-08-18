import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_campaign_driver as driver  # noqa: E402


class RoutingCampaignDriverTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "jobs": [
                {"runId": "a" * 24, "machineId": "machine-a"},
                {"runId": "b" * 24, "machineId": "machine-b"},
                {"runId": "c" * 24, "machineId": "machine-a"},
            ]
        }
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_result(self, run_id, status):
        path = self.root / "results" / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": status}))

    def args(self):
        return argparse.Namespace(
            protocol=ROOT / "protocols/routing-operational-v1.json",
            plan=Path("plan.json"), machine_id="machine-a", auth_file=Path("auth.json"),
            run_root=self.root, runtime_manifest=ROOT / "protocols/routing-runtime-v1.json",
            preflight=Path("preflight.json"), agent_timeout=30,
        )

    def test_status_counts_only_assigned_machine_and_finds_next(self):
        self.write_result("a" * 24, "PASS")
        observed = driver.status(self.plan, "machine-a", self.root)
        self.assertEqual(2, observed["jobs"])
        self.assertEqual(1, observed["pass"])
        self.assertEqual(1, observed["pending"])
        self.assertEqual("c" * 24, observed["nextRunId"])

    def test_status_can_limit_progress_to_the_authorized_stage(self):
        observed = driver.status(
            self.plan, "machine-a", self.root,
            authorized_run_ids={"c" * 24},
        )
        self.assertEqual(1, observed["jobs"])
        self.assertEqual(1, observed["pending"])
        self.assertEqual("c" * 24, observed["nextRunId"])

    def test_run_machine_resumes_successes_and_stops_on_infra(self):
        self.write_result("a" * 24, "PASS")
        with patch.object(driver.subprocess, "run") as run:
            run.return_value.returncode = 2
            with self.assertRaisesRegex(driver.DriverError, "paused"):
                driver.run_machine(self.args(), self.plan)
        run.assert_called_once()
        self.assertIn("c" * 24, run.call_args.args[0])

    def test_archive_moves_infra_artifacts_and_writes_hashed_manifest(self):
        run_id = "a" * 24
        self.write_result(run_id, "INFRA_FAILURE")
        transcript = self.root / "transcripts" / f"{run_id}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("event\n")
        workspace = self.root / "workspaces" / run_id
        workspace.mkdir(parents=True)
        (workspace / "candidate.json").write_text("{}")
        archived = driver.archive_infra_attempt(self.plan, "machine-a", self.root, run_id)
        self.assertFalse(driver.result_path(self.root, run_id).exists())
        manifest = json.loads((archived / "archive-manifest.json").read_text())
        self.assertEqual(run_id, manifest["runId"])
        self.assertIn(f"{run_id}.json", manifest["movedArtifacts"])
        self.assertEqual(64, len(manifest["inventory"][f"{run_id}.json"][0]["sha256"]))

    def test_archive_rejects_non_infra_and_wrong_machine(self):
        run_id = "a" * 24
        self.write_result(run_id, "PASS")
        with self.assertRaisesRegex(driver.DriverError, "INFRA_FAILURE"):
            driver.archive_infra_attempt(self.plan, "machine-a", self.root, run_id)
        with self.assertRaisesRegex(driver.DriverError, "not assigned"):
            driver.archive_infra_attempt(self.plan, "machine-b", self.root, run_id)

    def test_sequential_driver_launches_only_authorized_run_ids(self):
        args = self.args()
        args.sequential_state = self.root / "state.json"
        args.sequential_manifest = self.root / "manifest.json"
        state = {"authorized": [{"runIds": ["c" * 24]}]}
        with patch.object(driver, "_sequential_inputs", return_value=(None, state)), \
                patch.object(driver.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(0, driver.run_machine(args, self.plan, object()))
        run.assert_called_once()
        self.assertIn("c" * 24, run.call_args.args[0])
        self.assertNotIn("a" * 24, run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
