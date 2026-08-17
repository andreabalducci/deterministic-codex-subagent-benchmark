import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import harness


class HarnessTests(unittest.TestCase):
    id_key = b"k" * 32

    def test_plan_is_balanced_by_position_over_complete_cycle(self):
        plan = harness.make_plan(6, "fixed-seed", ["machine-a"], self.id_key)
        jobs = plan["jobs"]
        for configuration in harness.load_matrix():
            positions = sorted(
                job["orderPosition"] for job in jobs if job["id"] == configuration["id"]
            )
            self.assertEqual(list(range(6)), positions)

    def test_run_ids_are_opaque_and_reproducible(self):
        first = harness.make_plan(6, "same", ["machine-a"], self.id_key)
        second = harness.make_plan(6, "same", ["machine-a"], self.id_key)
        self.assertEqual(first, second)
        for job in first["jobs"]:
            self.assertEqual(16, len(job["runId"]))
            self.assertNotIn(job["id"], job["runId"])

        different_key = harness.make_plan(6, "same", ["machine-a"], b"z" * 32)
        self.assertNotEqual(
            [job["runId"] for job in first["jobs"]],
            [job["runId"] for job in different_key["jobs"]],
        )

    def test_every_machine_gets_every_order_position(self):
        machines = ["machine-a", "machine-b", "machine-c"]
        plan = harness.make_plan(18, "balanced", machines, self.id_key)
        for machine in machines:
            for configuration in harness.load_matrix():
                positions = sorted(
                    job["orderPosition"]
                    for job in plan["jobs"]
                    if job["machineId"] == machine and job["id"] == configuration["id"]
                )
                self.assertEqual(list(range(6)), positions)

        first_three = [job["machineId"] for job in plan["jobs"][::6]][:3]
        self.assertEqual(3, len(set(first_three)))

    def test_duplicate_machine_labels_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            harness.make_plan(12, "duplicate", ["same", "same"], self.id_key)

    def test_williams_rows_balance_direct_predecessors(self):
        rows = harness.williams_rows(harness.load_matrix(), "carryover")
        pairs = []
        for row in rows:
            pairs.extend((row[index]["id"], row[index + 1]["id"]) for index in range(5))
        self.assertEqual(30, len(pairs))
        self.assertEqual(30, len(set(pairs)))

    def test_path_traversal_run_ids_are_rejected(self):
        for value in ["../outside", "abc/def", "luna-high", "a" * 15, "g" * 16]:
            with self.assertRaises(ValueError):
                harness.validate_run_id(value)

    def test_timeout_bytes_are_json_safe(self):
        self.assertEqual("hello�", harness.decoded_timeout_stream(b"hello\xff"))

    def test_captured_output_is_bounded(self):
        value, exceeded = harness.read_capped(io.BytesIO(b"abcdef"), 4)
        self.assertEqual("abcd", value)
        self.assertTrue(exceeded)

    def test_live_process_output_is_bounded(self):
        result = harness.execute_captured(
            [sys.executable, "-c", "print('x' * 1000000)"],
            cwd=Path.cwd(), environment=os.environ.copy(), timeout=5, limit=1024,
        )
        self.assertTrue(result["outputLimitExceeded"])
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), 1024)

    def test_any_timeout_is_not_silently_counted_as_pass(self):
        timeout = {
            "command": ["x"], "exitCode": None, "stdout": "", "stderr": "",
            "timedOut": True, "launcherFailure": False, "durationSeconds": 1.0,
        }
        success = {
            "command": ["x"], "exitCode": 0, "stdout": "", "stderr": "",
            "timedOut": False, "launcherFailure": False, "durationSeconds": 0.1,
        }
        with patch("harness.run_process", side_effect=[timeout, success]):
            result = harness.run_with_timeout_confirmation(["x"], Path.cwd(), 1, "native")
        self.assertTrue(result["hadTimeout"])
        self.assertFalse(result["confirmedTimeout"])

    def test_materialized_workspace_excludes_hidden_and_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "opaque"
            harness.materialize(destination)
            self.assertTrue((destination / "TASK.md").exists())
            self.assertTrue((destination / "Cache.PublicTests").exists())
            self.assertFalse((destination / "Cache.HiddenTests").exists())
            self.assertFalse((destination / "reference").exists())

    def test_reference_candidate_integrity_is_clean(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate"
            harness.materialize(destination)
            (destination / "Cache.Core" / "AsyncExpiringCache.cs").write_bytes(
                harness.REFERENCE.read_bytes()
            )
            self.assertEqual([], harness.candidate_integrity(destination))
            self.assertEqual([], harness.policy_violations(destination))

    def test_candidate_symlinks_and_extra_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate"
            harness.materialize(destination)
            extra = destination / "Cache.Core" / "Extra.cs"
            extra.write_text("internal sealed class Extra {}", encoding="utf-8")
            self.assertIn("unexpected candidate file: Cache.Core/Extra.cs", harness.candidate_integrity(destination))
            extra.unlink()
            link = destination / "Cache.Core" / "Linked.cs"
            try:
                link.symlink_to(harness.REFERENCE)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")
            self.assertTrue(any("symbolic links are forbidden" in error for error in harness.candidate_integrity(destination)))

    def test_process_termination_and_module_initializers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate"
            harness.materialize(destination)
            implementation = destination / "Cache.Core" / "AsyncExpiringCache.cs"
            implementation.write_text(
                "using System.Runtime.CompilerServices; "
                "class X { [ModuleInitializer] internal static void M() => Environment.Exit(0); }",
                encoding="utf-8",
            )
            violations = harness.policy_violations(destination)
            self.assertTrue(any("module initializer" in value for value in violations))
            self.assertTrue(any("process termination" in value for value in violations))

    def test_untrusted_native_evaluation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate"
            harness.materialize(destination)
            with self.assertRaisesRegex(ValueError, "restricted to trusted"):
                harness.evaluate_candidate(
                    destination,
                    run_id="0123456789abcdef",
                    model="fixture",
                    effort="none",
                    machine="local",
                    repeat=1,
                    timeout=1,
                    backend="native",
                    isolation="test",
                )

    def test_aggregate_rejects_duplicate_run_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = harness.make_plan(6, "aggregate", ["machine-a"], self.id_key)
            plan_path = root / "plan.json"
            harness.save_json(plan_path, plan)
            job = plan["jobs"][0]
            result = {
                "schemaVersion": 1,
                "runId": job["runId"],
                "fixtureId": "async-cache-v1",
                "fixtureManifestHash": harness.sha256_file(harness.MANIFEST),
                "candidateHash": "c" * 64,
                "promptHash": harness.sha256_bytes(harness.AGENT_PROMPT.encode("utf-8")),
                "model": job["model"],
                "reasoningEffort": job["reasoningEffort"],
                "machineId": job["machineId"],
                "status": "CANDIDATE_FAILURE",
                "tests": [],
                "planHash": harness.sha256_file(plan_path),
                "trial": job["trial"],
                "orderPosition": job["orderPosition"],
                "provenance": {},
                "durationSeconds": 1.0,
                "generationDurationSeconds": 2.0,
                "repeatCount": 1,
                "backend": "docker",
                "evaluatorImage": {},
                "generatorImage": {},
            }
            result_path = root / "result.json"
            harness.save_json(result_path, result)
            with self.assertRaisesRegex(ValueError, "Duplicate run ID"):
                harness.aggregate(
                    [result_path, result_path], plan_path, allow_incomplete=True
                )

    def test_pass_with_failed_test_evidence_is_rejected(self):
        failed_attempt = {
            "command": ["dotnet", "test"],
            "exitCode": 99,
            "stdout": "",
            "stderr": "failed",
            "timedOut": True,
            "launcherFailure": True,
            "outputLimitExceeded": True,
            "durationSeconds": 1.0,
        }
        result = {
            "schemaVersion": 1,
            "runId": "0123456789abcdef",
            "fixtureId": "async-cache-v1",
            "fixtureManifestHash": "f" * 64,
            "candidateHash": "c" * 64,
            "promptHash": "p" * 64,
            "model": "model",
            "reasoningEffort": "high",
            "machineId": "machine",
            "status": "PASS",
            "tests": [
                {
                    "name": name,
                    "attempts": [dict(failed_attempt)],
                    "hadTimeout": True,
                    "confirmedTimeout": True,
                }
                for name in ["public-build", "public", "hidden-build", "hidden-1"]
            ],
            "planHash": "a" * 64,
            "trial": 0,
            "orderPosition": 0,
            "provenance": {},
            "generationDurationSeconds": 1.0,
            "repeatCount": 1,
            "backend": "docker",
            "evaluatorImage": {},
            "generatorImage": {},
            "durationSeconds": 1.0,
        }
        with self.assertRaisesRegex(ValueError, "failed evidence"):
            harness.validate_result(result)


if __name__ == "__main__":
    unittest.main()
