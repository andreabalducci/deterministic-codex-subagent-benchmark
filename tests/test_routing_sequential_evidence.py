import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import harness  # noqa: E402
import routing_campaign  # noqa: E402
import routing_evidence  # noqa: E402
import routing_sequential  # noqa: E402
import routing_sequential_evidence as evidence  # noqa: E402
import test_routing_evidence  # noqa: E402


class RoutingSequentialEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_routing_evidence.RoutingEvidenceTests.setUpClass()

    def inputs(self, root: Path):
        helper = test_routing_evidence.RoutingEvidenceTests(
            methodName="test_publish_is_deterministic_sanitized_and_replayable"
        )
        values = list(helper.make_inputs(root))
        protocol, runtime, plan, matrix, catalog, raw_results, _, provenance, fixture_root, candidate_root, preflights = values
        manifest = routing_sequential.make_manifest(plan, protocol)
        state = routing_sequential.make_initial_state(manifest, plan, protocol)
        all_results, _ = routing_evidence.sanitize_results(raw_results, plan, protocol)
        by_run = {result["runId"]: result for result in all_results}
        while not state["complete"]:
            state = routing_sequential.advance_state(
                state, manifest, plan, protocol, by_run
            )
        executed_ids = {
            item["runId"] for event in state["history"] for item in event["resultHashes"]
        }
        raw_subset = [result for result in raw_results if result["runId"] in executed_ids]
        sanitized, _ = routing_evidence.sanitize_results(raw_subset, plan, protocol)
        analysis = routing_sequential.analyze_state(
            state, manifest, plan, protocol,
            {result["runId"]: result for result in sanitized},
        )
        provenance = copy.deepcopy(provenance)
        provenance["analysisImplementation"] = {
            "name": "routing_sequential.py", "version": "1",
            "sha256": routing_evidence.sha256_file(Path(routing_sequential.__file__)),
        }
        return {
            "protocol": protocol, "runtime_manifest": runtime,
            "preflight_reports": preflights, "plan": plan, "matrix": matrix,
            "catalog": catalog, "sequential_manifest": manifest,
            "terminal_state": state, "raw_results": raw_subset,
            "raw_document_hashes": ["c" * 64], "analysis": analysis,
            "provenance": provenance, "fixture_root": fixture_root,
            "candidate_root": candidate_root,
        }, all_results

    def publish(self, root: Path, inputs: dict):
        return evidence.publish_bundle(root, **inputs)

    def resign(self, root: Path, artifact_key: str) -> None:
        bundle_path = root / "bundle.json"
        bundle = json.loads(bundle_path.read_text())
        relative = bundle["artifacts"][artifact_key]
        artifact = root / relative
        audit_path = root / "audit.json"
        audit = json.loads(audit_path.read_text())
        entry = next(item for item in audit["artifacts"] if item["path"] == relative)
        entry["sha256"] = routing_evidence.sha256_file(artifact)
        entry["bytes"] = artifact.stat().st_size
        routing_evidence._write_canonical(audit_path, audit)
        field = {
            "results": "resultsHash", "evaluationInputs": "resultsHash",
            "analysis": "analysisHash", "terminalState": "terminalStateHash",
        }.get(artifact_key)
        if field == "resultsHash":
            bundle[field] = routing_campaign.value_hash(json.loads((root / "results.json").read_text()))
        elif field:
            bundle[field] = routing_campaign.value_hash(json.loads(artifact.read_text()))
        bundle["auditHash"] = routing_campaign.value_hash(audit)
        routing_evidence._write_canonical(bundle_path, bundle)

    def test_publish_is_deterministic_strict_and_fully_replayable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, all_results = self.inputs(root)
            first = self.publish(root / "a", inputs)
            second = self.publish(root / "b", inputs)
            self.assertEqual(first, second)
            self.assertLess(first["counts"]["executedResults"], len(all_results))
            self.assertEqual(
                first["counts"]["potentialResults"] - first["counts"]["executedResults"],
                first["counts"]["savedResults"],
            )
            self.assertTrue(all(item["decision"] == "ACCEPT" for item in first["decisions"]))
            published_results = json.loads((root / "a" / "results.json").read_text())
            self.assertNotIn("privateTranscript", json.dumps(published_results))
            self.assertEqual(
                first["counts"]["executedResults"],
                len(json.loads((root / "a" / "evaluation-inputs.json").read_text())["candidates"]),
            )
            for document, schema_name in (
                (first, "routing-sequential-evidence-bundle.schema.json"),
                (json.loads((root / "a" / "audit.json").read_text()),
                 "routing-sequential-evidence-audit.schema.json"),
            ):
                harness.validate_schema_instance(
                    document, json.loads((ROOT / "schemas" / schema_name).read_text())
                )
            self.assertEqual(first, evidence.verify_bundle(root / "a"))

    def test_packaged_multifile_json_evaluator_is_replayed_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            candidate = root / "candidate"
            for directory in (fixture / "starter", fixture / "sealed", candidate):
                directory.mkdir(parents=True)
            descriptor = {
                "schemaVersion": 1, "adapter": "json-semantic-diff-v1",
                "mutable": ["one.json", "two.json"], "immutable": [],
                "required": ["TASK.md"], "rubric": [],
            }
            (fixture / "task.json").write_text(json.dumps(descriptor))
            for base in (fixture / "starter", candidate):
                (base / "TASK.md").write_text("task")
                (base / "one.json").write_text('{"value": 1}')
                (base / "two.json").write_text('{"value": 2}')
            for name, value in (("one.json", 1), ("two.json", 2)):
                (fixture / "sealed" / name).write_text(json.dumps({"value": value}))
            report = evidence._replay_evaluation(
                fixture, candidate, evaluator_image="sha256:" + "a" * 64
            )
            self.assertEqual("PASS", report["status"])
            (candidate / "two.json").write_text('{"value": 3}')
            self.assertEqual("FAIL", evidence._replay_evaluation(
                fixture, candidate, evaluator_image="sha256:" + "a" * 64
            )["status"])

    def test_missing_executed_result_is_rejected_even_when_hashes_are_updated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _ = self.inputs(root)
            self.publish(root / "bundle", inputs)
            path = root / "bundle" / "results.json"
            document = json.loads(path.read_text())
            document["records"].pop()
            routing_evidence._write_canonical(path, document)
            self.resign(root / "bundle", "results")
            with self.assertRaisesRegex(evidence.SequentialEvidenceError, "Missing executed"):
                evidence.verify_bundle(root / "bundle")

    def test_unexecuted_result_is_rejected_even_when_hashes_are_updated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, all_results = self.inputs(root)
            self.publish(root / "bundle", inputs)
            path = root / "bundle" / "results.json"
            document = json.loads(path.read_text())
            existing = {item["runId"] for item in document["records"]}
            extra = next(item for item in all_results if item["runId"] not in existing)
            document["records"].append(extra)
            order = {job["runId"]: index for index, job in enumerate(inputs["plan"]["jobs"])}
            document["records"].sort(key=lambda item: order[item["runId"]])
            routing_evidence._write_canonical(path, document)
            self.resign(root / "bundle", "results")
            with self.assertRaisesRegex(evidence.SequentialEvidenceError, "Unexecuted result"):
                evidence.verify_bundle(root / "bundle")

    def test_infrastructure_result_and_nonterminal_publication_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _ = self.inputs(root)
            changed = copy.deepcopy(inputs)
            changed["raw_results"][0]["status"] = "INFRA_FAILURE"
            changed["raw_results"][0]["failureKind"] = "generation-exit"
            with self.assertRaises((evidence.SequentialEvidenceError, routing_sequential.SequentialError)):
                self.publish(root / "infra", changed)

            changed = copy.deepcopy(inputs)
            changed["terminal_state"] = routing_sequential.make_initial_state(
                changed["sequential_manifest"], changed["plan"], changed["protocol"]
            )
            with self.assertRaisesRegex(evidence.SequentialEvidenceError, "terminal state"):
                self.publish(root / "active", changed)

    def test_analysis_state_and_candidate_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, _ = self.inputs(root)
            for name, artifact_key, mutate in (
                ("analysis", "analysis", lambda value: value.update({"savedJobs": 0})),
                ("state", "terminalState", lambda value: value.update({"complete": False})),
            ):
                bundle_root = root / name
                self.publish(bundle_root, inputs)
                path = bundle_root / evidence.ARTIFACT_PATHS[artifact_key]
                value = json.loads(path.read_text())
                mutate(value)
                routing_evidence._write_canonical(path, value)
                self.resign(bundle_root, artifact_key)
                with self.assertRaises(Exception):
                    evidence.verify_bundle(bundle_root)

            bundle_root = root / "candidate"
            self.publish(bundle_root, inputs)
            path = bundle_root / "evaluation-inputs.json"
            value = json.loads(path.read_text())
            record = next(iter(value["candidates"][0]["files"].values()))
            record["base64"] = "AAAA"
            routing_evidence._write_canonical(path, value)
            self.resign(bundle_root, "evaluationInputs")
            with self.assertRaises(Exception):
                evidence.verify_bundle(bundle_root)


if __name__ == "__main__":
    unittest.main()
