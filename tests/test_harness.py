import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import harness


class HarnessTests(unittest.TestCase):
    id_key = b"k" * 32

    @staticmethod
    def all_pass_behaviors():
        return [
            {"name": behavior, "outcome": "PASS"}
            for behavior in harness.HIDDEN_BEHAVIORS
        ]

    def campaign_result(
        self, job, plan_path, *, status="CANDIDATE_FAILURE", failure_kind="test"
    ):
        return {
            "schemaVersion": 1,
            "recordKind": "campaign",
            "runId": job["runId"],
            "fixtureId": harness.FIXTURE_ID,
            "fixtureManifestHash": harness.sha256_file(harness.MANIFEST),
            "candidateHash": "c" * 64,
            "promptHash": harness.sha256_bytes(harness.AGENT_PROMPT.encode("utf-8")),
            "model": job["model"],
            "reasoningEffort": job["reasoningEffort"],
            "machineId": job["machineId"],
            "status": status,
            "failureKind": None if status == "PASS" else failure_kind,
            "tests": [],
            "planHash": harness.sha256_file(plan_path),
            "trial": job["trial"],
            "orderPosition": job["orderPosition"],
            "provenance": {"gitCommit": None, "files": {}, "sdkVersion": "10.0.301"},
            "durationSeconds": 1.0,
            "generationDurationSeconds": 2.0,
            "repeatCount": 1,
            "backend": "docker",
            "evaluatorImage": {},
            "generatorImage": {},
            "isolation": "container-strong",
            "integrityViolations": [],
            "policyViolations": [],
            "manifestErrors": [],
            "platform": {"system": "test", "release": "test", "machine": "test", "python": "3.12"},
        }

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

    def test_multiplicity_adjusted_power_requires_90_samples_for_60_vs_85(self):
        self.assertEqual(
            90,
            harness.required_samples_per_configuration(0.60, 0.85),
        )

    def test_path_traversal_run_ids_are_rejected(self):
        for value in ["../outside", "abc/def", "luna-high", "a" * 15, "g" * 16]:
            with self.assertRaises(ValueError):
                harness.validate_run_id(value)

    def test_verification_run_ids_are_explicitly_validated(self):
        self.assertEqual(
            "verify-reference",
            harness.validate_run_id("verify-reference", allow_verify=True),
        )
        for value in ["verify-../../x", "verify-../../../x", "verify-", "verify-UPPER"]:
            with self.assertRaises(ValueError):
                harness.validate_run_id(value, allow_verify=True)
        with self.assertRaises(ValueError):
            harness.validate_run_id("verify-reference")

    def test_live_process_output_is_bounded(self):
        result = harness.execute_captured(
            [sys.executable, "-c", "print('x' * 1000000)"],
            cwd=Path.cwd(), environment=os.environ.copy(), timeout=5, limit=1024,
        )
        self.assertTrue(result["outputLimitExceeded"])
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), 1024)

    def test_stdout_and_stderr_share_one_output_limit(self):
        result = harness.execute_captured(
            [
                sys.executable,
                "-c",
                "import os; os.write(1, b'x' * 800); os.write(2, b'y' * 800)",
            ],
            cwd=Path.cwd(), environment=os.environ.copy(), timeout=5, limit=1000,
        )
        total = len(result["stdout"].encode()) + len(result["stderr"].encode())
        self.assertTrue(result["outputLimitExceeded"])
        self.assertLessEqual(total, 1000)

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

    def test_materialization_ignores_build_and_tool_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate"
            harness.materialize(destination)
            for name in ["bin", "obj", ".git", ".nuget", ".dotnet-home"]:
                artifact = destination / "Cache.Core" / name / "Injected.cs"
                artifact.parent.mkdir(parents=True)
                artifact.write_text("this is ignored", encoding="utf-8")
            self.assertEqual([], harness.candidate_integrity(destination))

    def test_replacement_never_deletes_workspace_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            workspace_root = runs / "workspaces"
            workspace_root.mkdir(parents=True)
            marker = workspace_root / "keep"
            marker.write_text("safe", encoding="utf-8")
            with patch.object(harness, "RUNS", runs):
                with self.assertRaisesRegex(ValueError, "strictly beneath"):
                    harness.materialize(workspace_root, replace=True)
            self.assertTrue(marker.exists())

    def test_private_json_write_is_atomic_and_mode_0600(self):
        if os.name == "nt":
            self.skipTest("POSIX modes are unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private.json"
            harness.save_json(path, {"value": 1}, private=True)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            harness.save_json(path, {"value": 2}, private=True)
            self.assertEqual({"value": 2}, json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual([], list(path.parent.glob(f".{path.name}.*")))

    def test_auth_symlink_is_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "auth.json"
            target.write_text("{}", encoding="utf-8")
            target.chmod(0o600)
            link = root / "auth-link.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(ValueError, "non-symlink"):
                harness.benchmark_auth_file(link)

    def test_non_utf8_source_is_structured_candidate_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate"
            harness.materialize(destination)
            implementation = destination / "Cache.Core" / "AsyncExpiringCache.cs"
            implementation.write_bytes(b"class X {}\xff")
            result = harness.evaluate_candidate(
                destination,
                run_id="0123456789abcdef",
                model="fixture",
                effort="none",
                machine="local",
                repeat=1,
                timeout=1,
                backend="native",
                isolation="fixture-verification",
                trusted=True,
            )
            self.assertEqual("CANDIDATE_FAILURE", result["status"])
            self.assertEqual("integrity", result["failureKind"])
            self.assertTrue(any("non-UTF-8" in value for value in result["integrityViolations"]))

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
            result = self.campaign_result(job, plan_path)
            result_path = root / "result.json"
            harness.save_json(result_path, result)
            with self.assertRaisesRegex(ValueError, "Duplicate run ID"):
                harness.aggregate(
                    [result_path, result_path], plan_path, allow_incomplete=True
                )

    def test_external_evaluation_validates_but_aggregate_rejects_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = harness.make_plan(6, "external", ["machine-a"], self.id_key)
            plan_path = root / "plan.json"
            harness.save_json(plan_path, plan)
            job = plan["jobs"][0]
            result = self.campaign_result(job, plan_path)
            result["recordKind"] = "external-evaluation"
            for field in ["planHash", "trial", "orderPosition", "generationDurationSeconds"]:
                result.pop(field)
            harness.validate_result(result)
            result_path = root / "external.json"
            harness.save_json(result_path, result)
            with self.assertRaisesRegex(ValueError, "rejects external-evaluation"):
                harness.aggregate([result_path], plan_path, allow_incomplete=True)

    def test_timeout_classification_distinguishes_candidate_build_and_anomaly(self):
        def evidence(first_timeout, second_timeout):
            attempts = []
            for timed_out in [first_timeout, second_timeout]:
                attempts.append({
                    "command": ["x"], "exitCode": None if timed_out else 0,
                    "stdout": "", "stderr": "", "timedOut": timed_out,
                    "launcherFailure": False, "outputLimitExceeded": False,
                    "durationSeconds": 1.0,
                })
            return {
                "attempts": attempts, "hadTimeout": first_timeout,
                "confirmedTimeout": second_timeout,
            }

        confirmed = evidence(True, True)
        self.assertEqual(
            ("CANDIDATE_FAILURE", "timeout"),
            harness.execution_failure(confirmed, candidate_execution=True),
        )
        self.assertEqual(
            ("INFRA_FAILURE", "build-timeout"),
            harness.execution_failure(confirmed, candidate_execution=False),
        )
        anomaly = evidence(True, False)
        self.assertEqual(
            ("INFRA_FAILURE", "timeout-anomaly"),
            harness.execution_failure(anomaly, candidate_execution=True),
        )

    def test_generation_failures_are_recorded_without_resampling(self):
        base = {
            "exitCode": 0, "timedOut": False, "launcherFailure": False,
            "outputLimitExceeded": False,
        }
        cases = [
            ({**base, "launcherFailure": True, "exitCode": None},
             ("INFRA_FAILURE", "generation-launcher")),
            ({**base, "timedOut": True, "exitCode": None},
             ("CANDIDATE_FAILURE", "generation-timeout")),
            ({**base, "outputLimitExceeded": True},
             ("CANDIDATE_FAILURE", "generation-output-limit")),
            ({**base, "exitCode": 1},
             ("CANDIDATE_FAILURE", "generation-exit")),
            (base, None),
        ]
        for metadata, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, harness.generation_failure_kind(metadata))

    def test_hidden_behavior_outcomes_are_structured_without_assertion_text(self):
        output = "\n".join([
            "PASS constructor validation",
            "FAIL same-key misses are single flight: InvalidOperationException: SECRET",
        ])
        self.assertEqual(
            [
                {"name": "constructor validation", "outcome": "PASS"},
                {"name": "same-key misses are single flight", "outcome": "FAIL"},
                *[
                    {"name": behavior, "outcome": "NOT_RUN"}
                    for behavior in harness.HIDDEN_BEHAVIORS[2:]
                ],
            ],
            harness.extract_behavior_outcomes(output),
        )
        ambiguous = output + "\nPASS same-key misses are single flight"
        self.assertEqual(
            "AMBIGUOUS",
            harness.extract_behavior_outcomes(ambiguous)[1]["outcome"],
        )

    def test_incomplete_aggregate_uses_intent_to_treat_denominator(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = harness.make_plan(6, "itt", ["machine-a"], self.id_key)
            plan_path = root / "plan.json"
            harness.save_json(plan_path, plan)
            job = plan["jobs"][0]
            result = self.campaign_result(job, plan_path, status="PASS", failure_kind=None)
            success_attempt = {
                "command": ["dotnet"], "exitCode": 0,
                "stdout": harness.PUBLIC_PASS_MARKER, "stderr": "",
                "timedOut": False, "launcherFailure": False,
                "outputLimitExceeded": False, "durationSeconds": 0.1,
            }
            hidden_attempt = {
                **success_attempt,
                "stdout": "\n".join(
                    [f"PASS {behavior}" for behavior in harness.HIDDEN_BEHAVIORS]
                    + [harness.HIDDEN_PASS_MARKER]
                ),
            }
            result["tests"] = [
                {"name": "public-build", "attempts": [success_attempt], "hadTimeout": False, "confirmedTimeout": False},
                {"name": "public", "attempts": [success_attempt], "hadTimeout": False, "confirmedTimeout": False},
                {"name": "hidden-build", "attempts": [success_attempt], "hadTimeout": False, "confirmedTimeout": False},
                {"name": "hidden-1", "behaviors": self.all_pass_behaviors(), "attempts": [hidden_attempt], "hadTimeout": False, "confirmedTimeout": False},
            ]
            result_path = root / "result.json"
            harness.save_json(result_path, result)
            summary = harness.aggregate([result_path], plan_path, allow_incomplete=True)
            group = summary["groups"][f"{job['model']}:{job['reasoningEffort']}"]
            self.assertEqual(6, group["plannedRuns"])
            self.assertEqual(1 / 6, group["passRate"])

    def test_publish_bundle_redacts_hidden_output_and_includes_audit_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = harness.make_plan(6, "publish", ["machine-a"], self.id_key)
            plan_path = root / "plan.json"
            harness.save_json(plan_path, plan)
            paths = []
            for index, job in enumerate(plan["jobs"]):
                result = self.campaign_result(job, plan_path)
                if index == 0:
                    result["tests"] = [{
                        "name": "hidden-1",
                        "behaviors": [
                            {"name": behavior, "outcome": "FAIL" if index == 0 else "NOT_RUN"}
                            for index, behavior in enumerate(harness.HIDDEN_BEHAVIORS)
                        ],
                        "attempts": [{
                            "command": ["dotnet"], "exitCode": 1,
                            "stdout": "FAIL constructor validation: SECRET HIDDEN ASSERTION",
                            "stderr": "SECRET DETAIL",
                            "timedOut": False, "launcherFailure": False,
                            "outputLimitExceeded": False, "durationSeconds": 0.1,
                        }],
                        "hadTimeout": False, "confirmedTimeout": False,
                    }]
                result_path = root / f"result-{index}.json"
                harness.save_json(result_path, result)
                paths.append(result_path)
            bundle = harness.publish_evidence_bundle(paths, plan_path)
            encoded = json.dumps(bundle)
            self.assertNotIn("SECRET", encoded)
            self.assertEqual(plan, bundle["protocol"]["plan"])
            self.assertEqual(harness.load_matrix(), bundle["protocol"]["matrix"])
            self.assertEqual(harness.FIXTURE_ID, bundle["protocol"]["fixtureManifest"]["fixtureId"])
            harness.validate_schema_instance(bundle, harness.load_evidence_schema())
            malformed = json.loads(json.dumps(bundle))
            malformed["outcomes"][0]["unexpected"] = True
            with self.assertRaisesRegex(ValueError, "additional property"):
                harness.validate_schema_instance(malformed, harness.load_evidence_schema())
            contradictory = json.loads(json.dumps(bundle))
            contradictory["outcomes"][0]["status"] = "PASS"
            with self.assertRaises(ValueError):
                harness.validate_evidence_bundle(contradictory)
            inconsistent = json.loads(json.dumps(bundle))
            inconsistent["outcomes"][0]["status"] = "PASS"
            inconsistent["outcomes"][0]["failureKind"] = None
            with self.assertRaisesRegex(ValueError, "aggregate does not match"):
                harness.validate_evidence_bundle(inconsistent)
            published = next(outcome for outcome in bundle["outcomes"] if outcome["tests"])
            self.assertEqual(
                [
                    {"name": behavior, "outcome": "FAIL" if index == 0 else "NOT_RUN"}
                    for index, behavior in enumerate(harness.HIDDEN_BEHAVIORS)
                ],
                published["tests"][0]["behaviors"],
            )
            attempt = published["tests"][0]["attempts"][0]
            self.assertNotIn("stdout", attempt)
            self.assertIn("stdoutSha256", attempt)
            self.assertIn(plan["jobs"][0]["runId"], bundle["audit"]["sourceResultHashes"])

    def test_publish_rejects_incomplete_cohort(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = harness.make_plan(6, "publish-incomplete", ["machine-a"], self.id_key)
            plan_path = root / "plan.json"
            harness.save_json(plan_path, plan)
            result = self.campaign_result(plan["jobs"][0], plan_path)
            result_path = root / "result.json"
            harness.save_json(result_path, result)
            with self.assertRaisesRegex(ValueError, "complete resolved cohort"):
                harness.publish_evidence_bundle([result_path], plan_path)

    def test_docker_commands_run_as_calling_posix_user(self):
        if os.name != "posix":
            self.skipTest("POSIX identity is unavailable")
        command = harness.base_command(["true"], Path.cwd(), "docker", "test", readonly=False)
        self.assertIn("--user", command)
        self.assertIn(f"{os.getuid()}:{os.getgid()}", command)
        self.assertTrue(any(f"uid={os.getuid()}" in value for value in command))

    def test_private_create_only_json_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "result.json"
            harness.save_json(path, {"first": True}, private=True, replace=False)
            with self.assertRaises(FileExistsError):
                harness.save_json(path, {"second": True}, private=True, replace=False)
            self.assertEqual({"first": True}, json.loads(path.read_text(encoding="utf-8")))

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
            "recordKind": "campaign",
            "runId": "0123456789abcdef",
            "fixtureId": harness.FIXTURE_ID,
            "fixtureManifestHash": "f" * 64,
            "candidateHash": "c" * 64,
            "promptHash": "d" * 64,
            "model": "model",
            "reasoningEffort": "high",
            "machineId": "machine",
            "status": "PASS",
            "failureKind": None,
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
            "provenance": {"gitCommit": None, "files": {}, "sdkVersion": "10.0.301"},
            "generationDurationSeconds": 1.0,
            "repeatCount": 1,
            "backend": "docker",
            "evaluatorImage": {},
            "generatorImage": {},
            "durationSeconds": 1.0,
            "isolation": "container-strong",
            "integrityViolations": [],
            "policyViolations": [],
            "manifestErrors": [],
            "platform": {"system": "test", "release": "test", "machine": "test", "python": "3.12"},
        }
        result["tests"][-1]["behaviors"] = self.all_pass_behaviors()
        with self.assertRaisesRegex(ValueError, "failed evidence"):
            harness.validate_result(result)


if __name__ == "__main__":
    unittest.main()
