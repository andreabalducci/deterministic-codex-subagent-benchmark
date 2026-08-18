import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import routing_tasks


class RoutingTaskTests(unittest.TestCase):
    def test_catalog_has_required_family_coverage_and_surfaces(self):
        catalog = routing_tasks.load_catalog()
        self.assertEqual(84, len(catalog["tasks"]))
        self.assertEqual(routing_tasks.FAMILIES, {task["family"] for task in catalog["tasks"]})
        self.assertGreaterEqual(len({task["ecosystem"] for task in catalog["tasks"]}), 3)
        for family in routing_tasks.FAMILIES:
            tasks = [task for task in catalog["tasks"] if task["family"] == family]
            self.assertEqual(2, sum(task["development"] for task in tasks))
            self.assertEqual(12, sum(not task["development"] for task in tasks))
            self.assertEqual(14, len({task["template"] for task in tasks}))
            self.assertGreaterEqual(len({task["ecosystem"] for task in tasks}), 3)

    def test_calibration_references_pass_and_mutants_fail(self):
        report = routing_tasks.calibrate()
        self.assertTrue(report["passed"])
        self.assertEqual(294, len(report["cases"]))
        self.assertEqual({"PASS", "FAIL"}, {case["actual"] for case in report["cases"]})
        self.assertEqual(
            84,
            sum(case["case"] == "equivalent-positive" for case in report["cases"]),
        )
        self.assertEqual(
            42,
            sum(case["case"] == "schema-extra-mutant" for case in report["cases"]),
        )

    def test_docker_calibration_artifact_binds_catalog_and_image(self):
        report = {"passed": True, "cases": [{"case": "reference"}]}
        with patch.object(routing_tasks.harness, "docker_image_info", return_value={
            "id": "sha256:" + "a" * 64,
        }):
            artifact = routing_tasks.docker_calibration_artifact(report)
        self.assertEqual("routing-calibration", artifact["recordKind"])
        self.assertEqual("docker", artifact["backend"])
        self.assertEqual("sha256:" + "a" * 64, artifact["evaluatorImageId"])
        self.assertEqual(report["cases"], artifact["cases"])
        self.assertEqual(64, len(artifact["catalogHash"]))

    def test_materialization_never_copies_sealed_answers_or_mutants(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "candidate"
            routing_tasks.materialize("read-heavy-01", target)
            self.assertTrue((target / "answer.json").exists())
            self.assertFalse((target / "sealed").exists())
            self.assertFalse((target / "reference").exists())
            self.assertFalse((target / "mutants").exists())

    def test_path_traversal_is_rejected(self):
        for value in ("../sealed", "/tmp/nope", "routing/../mechanical", ""):
            with self.assertRaises(ValueError):
                routing_tasks.safe_relative(value)

    def test_template_manifest_detects_fixture_mutation(self):
        task = routing_tasks.task_by_id("read-heavy-01")
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "template"
            __import__("shutil").copytree(routing_tasks.template_root(task), copied)
            (copied / "starter" / "answer.json").write_text("{}", encoding="utf-8")
            with patch.object(routing_tasks, "template_root", return_value=copied):
                with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                    routing_tasks.verify_template_manifest(task)

    def test_mechanical_json_evaluation_is_semantic_and_rejects_immutable_change(self):
        task_id = "mechanical-01"
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            routing_tasks.materialize(task_id, candidate)
            expected = routing_tasks.template_root(routing_tasks.task_by_id(task_id)) / "sealed" / "src" / "CacheOptions.json"
            (candidate / "src" / "CacheOptions.json").write_bytes(expected.read_bytes())
            first = routing_tasks.evaluate_artifact(task_id, candidate)
            second = routing_tasks.evaluate_artifact(task_id, candidate)
            self.assertEqual(first, second)
            self.assertEqual("PASS", first["status"])
            parsed = json.loads((candidate / "src" / "CacheOptions.json").read_text())
            (candidate / "src" / "CacheOptions.json").write_text(
                json.dumps(dict(reversed(list(parsed.items()))), separators=(",", ":"))
            )
            self.assertEqual(
                "PASS", routing_tasks.evaluate_artifact(task_id, candidate)["status"]
            )
            (candidate / "TASK.md").write_text("tampered", encoding="utf-8")
            self.assertTrue(routing_tasks.evaluate_artifact(task_id, candidate)["integrityViolations"])

    def test_mechanical_prompts_define_unique_concrete_edits(self):
        tasks = [
            task for task in routing_tasks.load_catalog()["tasks"]
            if task["family"] == "mechanical"
        ]
        prompts = [
            (routing_tasks.template_root(task) / "starter" / "TASK.md").read_text()
            for task in tasks
        ]
        self.assertEqual(14, len(set(prompts)))
        self.assertTrue(all("Change only" in prompt or "Append py312" in prompt for prompt in prompts))
        self.assertFalse(any("Inspect the visible source and apply the requested" in prompt for prompt in prompts))

    def test_bounded_mapping_evaluator_is_semantic_and_preserves_existing_entries(self):
        task_id = "bounded-patch-01"
        task = routing_tasks.task_by_id(task_id)
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            routing_tasks.materialize(task_id, candidate)
            relative = "src/claim_properties.json"
            value = json.loads((candidate / relative).read_text())
            value["tenant_id"] = "tenantId"
            # Non-canonical formatting and order must not affect a semantic mapping check.
            (candidate / relative).write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
            self.assertEqual(
                "PASS",
                routing_tasks.evaluate_artifact(
                    task_id, candidate, backend="native", trusted_native=True
                )["status"],
            )
            value["sub"] = "wrongSubject"
            (candidate / relative).write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(
                "FAIL",
                routing_tasks.evaluate_artifact(
                    task_id, candidate, backend="native", trusted_native=True
                )["status"],
            )

        self.assertEqual("command-test-v1", task["adapter"])

    def test_docker_evaluator_mount_excludes_references_and_mutants(self):
        task_id = "bounded-patch-01"
        task = routing_tasks.task_by_id(task_id)
        candidate = routing_tasks.template_root(task) / "reference"

        def execute(command, **_kwargs):
            mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
            fixture_mount = next(value for value in mounts if "dst=/fixture" in value)
            source = Path(fixture_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            self.assertTrue((source / "sealed").is_dir())
            self.assertTrue((source / "starter").is_dir())
            self.assertFalse((source / "reference").exists())
            self.assertFalse((source / "mutants").exists())
            return {
                "exitCode": 0, "timedOut": False, "launcherFailure": False,
                "outputLimitExceeded": False, "stdout": "", "stderr": "",
                "durationSeconds": 0.01,
            }

        with patch.object(routing_tasks.harness, "execute_captured", side_effect=execute):
            report = routing_tasks.evaluate_artifact(task_id, candidate, backend="docker")
        self.assertEqual("PASS", report["status"])

    def test_implementation_fixtures_use_behavioral_or_data_contract_evaluators(self):
        expected_kinds = {
            "dotnet-behavior": 4,
            "node-behavior": 4,
            "python-behavior": 3,
            "json-contract": 2,
            "sqlite-migration": 1,
        }
        actual_kinds = {}
        tasks = [
            task for task in routing_tasks.load_catalog()["tasks"]
            if task["family"] == "isolated-implementation"
        ]
        for task in tasks:
            evaluator = json.loads(
                (routing_tasks.template_root(task) / "sealed" / "evaluator.json").read_text()
            )
            kind = evaluator["testKind"]
            actual_kinds[kind] = actual_kinds.get(kind, 0) + 1
            self.assertNotIn("requiredStrings", evaluator)
            self.assertNotIn("forbiddenStrings", evaluator)
            self.assertNotEqual("source-contract", kind)
        self.assertEqual(expected_kinds, actual_kinds)

    def test_artifact_rubric_requires_all_sealed_fields(self):
        task_id = "coordination-01"
        task = routing_tasks.task_by_id(task_id)
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            routing_tasks.materialize(task_id, candidate)
            sealed = json.loads((routing_tasks.template_root(task) / "sealed" / "expected.json").read_text())
            (candidate / "answer.json").write_text(json.dumps(sealed), encoding="utf-8")
            self.assertEqual("PASS", routing_tasks.evaluate_artifact(task_id, candidate)["status"])
            # Change a field that is actually part of this fixture's frozen rubric.
            sealed["integration"] = "wrong"
            (candidate / "answer.json").write_text(json.dumps(sealed), encoding="utf-8")
            self.assertEqual("FAIL", routing_tasks.evaluate_artifact(task_id, candidate)["status"])

    def test_artifact_rubric_allows_restated_prose_but_rejects_extra_fields(self):
        task_id = "high-risk-01"
        task = routing_tasks.task_by_id(task_id)
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "candidate"
            routing_tasks.materialize(task_id, candidate)
            expected = json.loads(
                (routing_tasks.template_root(task) / "sealed" / "expected.json").read_text()
            )
            expected["summary"] += " — same evidence, restated."
            (candidate / "answer.json").write_text(json.dumps(expected))
            report = routing_tasks.evaluate_artifact(task_id, candidate)
            self.assertEqual("PASS", report["status"])
            self.assertTrue(report["criticalPassed"])
            self.assertEqual(1.0, report["rubricScore"])
            expected["unexpected"] = True
            (candidate / "answer.json").write_text(json.dumps(expected))
            report = routing_tasks.evaluate_artifact(task_id, candidate)
            self.assertEqual("FAIL", report["status"])
            self.assertFalse(report["schemaValid"])


if __name__ == "__main__":
    unittest.main()
