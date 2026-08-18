import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_campaign  # noqa: E402
import routing_evidence  # noqa: E402
import routing_preflight  # noqa: E402
import harness  # noqa: E402


class RoutingEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads(
            (ROOT / "protocols" / "routing-v1.json").read_text(encoding="utf-8")
        )

    def make_inputs(self, root: Path):
        protocol = copy.deepcopy(self.frozen)
        protocol["protocolId"] = "routing-evidence-synthetic"
        protocol["bootstrapSamples"] = 100
        protocol["replicatesPerFixture"] = 3
        protocol["machines"] = ["test-machine"]
        protocol.setdefault("robustness", {
            "maximumQualityFloorShortfall": 0.10,
            "maximumDecisionGainShortfall": 0.10,
            "maximumFixtureCandidateRateRange": 0.50,
            "maximumFixtureDecisionGainRange": 0.50,
        })
        tasks = []
        fixture_root = root / "fixture-source"
        for family in protocol["families"]:
            fixture_ids = [f"{family['id']}-fixture-{suffix}" for suffix in "abcdef"]
            family["heldOutFixtureIds"] = fixture_ids
            family["heldOutFixtureEcosystems"] = [
                "synthetic-a", "synthetic-b", "synthetic-c",
                "synthetic-a", "synthetic-b", "synthetic-c",
            ]
            for fixture_index, fixture_id in enumerate(fixture_ids):
                content_hash = hashlib.sha256(fixture_id.encode()).hexdigest()
                template = f"routing/{fixture_id}"
                manifest = {"schemaVersion": 1, "contentHash": content_hash}
                path = fixture_root / template / "fixture-manifest.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(manifest), encoding="utf-8")
                (path.parent / "task.json").write_text(json.dumps({
                    "schemaVersion": 1, "adapter": "exact-file-v1",
                    "mutable": ["result.txt"], "immutable": [],
                    "required": ["TASK.md"], "rubric": [],
                }), encoding="utf-8")
                (path.parent / "starter").mkdir()
                (path.parent / "starter" / "TASK.md").write_text("synthetic task", encoding="utf-8")
                (path.parent / "starter" / "result.txt").write_text("starter", encoding="utf-8")
                (path.parent / "sealed").mkdir()
                (path.parent / "sealed" / "expected.json").write_text("{}", encoding="utf-8")
                (path.parent / "sealed" / "result.txt").write_text("correct", encoding="utf-8")
                tasks.append({
                    "id": fixture_id,
                    "family": family["catalogFamilyId"],
                    "kind": "confirmatory",
                    "development": False,
                    "ecosystem": family["heldOutFixtureEcosystems"][fixture_index],
                    "template": template,
                    "manifestHash": content_hash,
                })
        routing_campaign.validate_protocol(protocol)
        runtime_manifest = json.loads(
            (ROOT / "protocols" / "routing-runtime-v1.json").read_text(encoding="utf-8")
        )
        matrix = {"configurations": copy.deepcopy(protocol["matrix"])}
        catalog = {"schemaVersion": 2, "tasks": tasks}
        models = [{
            "id": model, "model": model,
            "supportedReasoningEfforts": [
                {"reasoningEffort": effort} for effort in ("low", "medium", "high")
            ],
            "serviceTiers": [{"id": "priority"}], "additionalSpeedTiers": ["fast"],
        } for model in sorted({item["model"] for item in protocol["matrix"]})]
        preflight_image = {
            "tag": "routing-generator:test", "id": "sha256:" + "3" * 64,
            "repoDigests": ["routing-generator@sha256:" + "3" * 64],
            "os": "linux", "architecture": "amd64", "specHash": "4" * 64,
        }
        preflights = [routing_preflight.make_report(
            protocol, runtime_manifest, models, preflight_image,
            runtime_manifest["codexVersion"], machine,
        ) for machine in protocol["machines"]]
        plan = routing_campaign.make_plan(
            protocol, b"0123456789abcdef", preflights, runtime_manifest
        )
        family_by_id = {family["id"]: family for family in protocol["families"]}
        results = []
        candidate_root = root / "candidate-source"
        generator_digest = "3" * 64
        evaluator_digest = "b" * 64
        generator_image = {
            "tag": "routing-generator:test", "id": "sha256:" + generator_digest,
            "repoDigests": ["routing-generator@sha256:" + generator_digest],
            "os": "linux", "architecture": "amd64", "specHash": "4" * 64,
        }
        evaluator_image = {
            "tag": "routing-evaluator:test", "id": "sha256:" + evaluator_digest,
            "repoDigests": ["routing-evaluator@sha256:" + evaluator_digest],
            "os": "linux", "architecture": "amd64", "specHash": "5" * 64,
        }
        for job in plan["jobs"]:
            family = family_by_id[job["familyId"]]
            candidate = job["treatmentId"] == family["candidateId"]
            decision_comparator = job["treatmentId"] == family["decisionComparatorId"]
            status = "PASS"
            if family["recommendationType"] == "capability" and decision_comparator:
                status = "CANDIDATE_FAILURE"
            workspace = candidate_root / job["runId"]
            workspace.mkdir(parents=True)
            (workspace / "TASK.md").write_text("synthetic task", encoding="utf-8")
            (workspace / "result.txt").write_text(
                "correct" if status == "PASS" else "wrong", encoding="utf-8"
            )
            candidate_hash = hashlib.sha256(json.dumps({
                "result.txt": hashlib.sha256((workspace / "result.txt").read_bytes()).hexdigest()
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            report = {
                "schemaVersion": 2, "taskId": job["fixtureId"],
                "family": job["familyId"], "adapter": "exact-file-v1",
                "candidateHash": candidate_hash,
                "status": "PASS" if status == "PASS" else "FAIL",
                "integrityViolations": [],
                "outcomes": [{"id": "exact-file", "outcome": "PASS" if status == "PASS" else "FAIL"}],
            }
            result = {
                "schemaVersion": 2,
                "recordKind": "routing-result",
                "protocolHash": routing_campaign.value_hash(protocol),
                "planHash": routing_campaign.value_hash(plan),
                "runtimeManifestHash": protocol["runtimeManifestHash"],
                "preflightReportHash": plan["preflightBindings"][0]["reportHash"],
                "capabilityDigest": plan["preflightBindings"][0]["capabilityDigest"],
                "runId": job["runId"],
                "familyId": job["familyId"],
                "fixtureId": job["fixtureId"],
                "replicate": job["replicate"],
                "machineId": job["machineId"],
                "orderPosition": job["orderPosition"],
                "treatmentId": job["treatmentId"],
                "model": job["model"],
                "reasoningEffort": job["reasoningEffort"],
                "status": status,
                "failureKind": None if status == "PASS" else "evaluation-failed",
                "fixtureManifestHash": next(
                    task["manifestHash"] for task in tasks if task["id"] == job["fixtureId"]
                ),
                "promptHash": "1" * 64,
                "candidateHash": candidate_hash,
                "transcriptHash": hashlib.sha256(job["runId"].encode()).hexdigest(),
                "generation": {
                    "backend": "docker", "isolation": "container-strong",
                    "generatorImage": generator_image, "codexVersion": runtime_manifest["codexVersion"],
                    "attemptHash": hashlib.sha256(("attempt:" + job["runId"]).encode()).hexdigest(),
                    "requestedModel": job["model"],
                    "requestedReasoningEffort": job["reasoningEffort"],
                    "requestedServiceTier": runtime_manifest["serviceTier"],
                    "fastMode": runtime_manifest["fastMode"],
                    "multiAgent": False, "observedModel": job["model"],
                    "serviceTier": runtime_manifest["serviceTier"],
                    "runtimeVerification": "telemetry-confirmed",
                },
                "usage": {
                    "inputTokens": 100, "cachedInputTokens": 0, "outputTokens": 20,
                    "reasoningOutputTokens": 10, "cacheWriteInputTokens": 0,
                    "totalTokens": 120,
                },
                "evaluation": {
                    "backend": "docker", "evaluatorProfile": "routing-evaluator-v1",
                    "evaluatorImage": evaluator_image,
                    "reportHash": routing_campaign.value_hash(report),
                },
                "generationDurationSeconds": 1.0 if candidate else 2.0,
                "evaluationDurationSeconds": 0.25,
                "totalDurationSeconds": (1.0 if candidate else 2.0) + 0.25,
                "provenance": {
                    "gitCommit": "a" * 40, "files": {},
                    "platform": {
                        "system": "test", "release": "test", "machine": "test",
                        "python": "test",
                    },
                },
                "privateTranscript": "must never be published",
            }
            results.append(result)
        sanitized, discarded = routing_evidence.sanitize_results(results, plan, protocol)
        self.assertEqual(["privateTranscript"], discarded)
        analysis = routing_campaign.analyze(protocol, plan, sanitized)
        component_hash = hashlib.sha256((ROOT / "routing_campaign.py").read_bytes()).hexdigest()
        provenance = {
            "schemaVersion": 1,
            "recordKind": "routing-provenance",
            "sourceRepository": {"url": None, "commit": "a" * 40, "dirty": False},
            "campaignRunner": {"name": "test runner", "version": "1", "sha256": component_hash},
            "analysisImplementation": {"name": "routing_campaign.py", "version": "1", "sha256": component_hash},
            "configurations": [{
                "id": item["id"],
                "model": item["model"],
                "reasoningEffort": item["reasoningEffort"],
                "serviceTier": runtime_manifest["serviceTier"],
                "toolVersion": runtime_manifest["codexVersion"],
            } for item in protocol["matrix"]],
            "evaluator": {
                "name": "routing evaluator", "version": "1",
                "imageDigest": "sha256:" + evaluator_digest,
            },
            "machines": [{"id": "test-machine", "platform": "synthetic"}],
        }
        return protocol, runtime_manifest, plan, matrix, catalog, results, analysis, provenance, fixture_root, candidate_root, preflights

    def publish(self, output: Path, inputs):
        protocol, runtime_manifest, plan, matrix, catalog, results, analysis, provenance, fixture_root, candidate_root, preflights = inputs
        return routing_evidence.publish_bundle(
            output,
            protocol=protocol,
            runtime_manifest=runtime_manifest,
            preflight_reports=preflights,
            plan=plan,
            matrix=matrix,
            catalog=catalog,
            raw_results=results,
            raw_document_hashes=["c" * 64],
            analysis=analysis,
            provenance=provenance,
            fixture_root=fixture_root,
            candidate_root=candidate_root,
        )

    def test_publish_is_deterministic_sanitized_and_replayable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.make_inputs(root)
            first = self.publish(root / "bundle-a", inputs)
            second = self.publish(root / "bundle-b", inputs)
            self.assertEqual(first, second)
            self.assertEqual(
                (root / "bundle-a" / "bundle.json").read_bytes(),
                (root / "bundle-b" / "bundle.json").read_bytes(),
            )
            results = json.loads((root / "bundle-a" / "results.json").read_text())
            self.assertNotIn("privateTranscript", json.dumps(results))
            self.assertIn("generation", results["records"][0])
            self.assertIn("usage", results["records"][0])
            self.assertEqual(2, results["records"][0]["schemaVersion"])
            audit = json.loads((root / "bundle-a" / "audit.json").read_text())
            self.assertEqual(["privateTranscript"], audit["sanitization"]["discardedFields"])
            for document, schema_name in (
                (first, "routing-evidence-bundle.schema.json"),
                (audit, "routing-evidence-audit.schema.json"),
                (inputs[7], "routing-provenance.schema.json"),
            ):
                schema = json.loads((ROOT / "schemas" / schema_name).read_text())
                harness.validate_schema_instance(document, schema)
            verified = routing_evidence.verify_bundle(root / "bundle-a")
            self.assertEqual(first, verified)

    def test_payload_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.make_inputs(root)
            self.publish(root / "bundle", inputs)
            analysis_path = root / "bundle" / "analysis.json"
            analysis = json.loads(analysis_path.read_text())
            analysis["families"][0]["decision"] = "CONTRADICTED"
            analysis_path.write_bytes(routing_evidence.canonical_bytes(analysis))
            with self.assertRaises((routing_evidence.EvidenceError, routing_campaign.ValidationError)):
                routing_evidence.verify_bundle(root / "bundle")

    def test_machine_preflight_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.make_inputs(root)
            self.publish(root / "bundle", inputs)
            path = root / "bundle" / "preflight-reports.json"
            document = json.loads(path.read_text())
            document["reports"][0]["generatorImage"]["architecture"] = "different"
            path.write_bytes(routing_evidence.canonical_bytes(document))
            with self.assertRaises((routing_evidence.EvidenceError, routing_campaign.ValidationError)):
                routing_evidence.verify_bundle(root / "bundle")

    def test_asserted_candidate_failure_is_recomputed_from_packaged_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = list(self.make_inputs(root))
            results = inputs[5]
            passing = next(item for item in results if item["status"] == "PASS")
            passing["status"] = "CANDIDATE_FAILURE"
            passing["failureKind"] = "evaluation-failed"
            sanitized, _ = routing_evidence.sanitize_results(results, inputs[2], inputs[0])
            inputs[6] = routing_campaign.analyze(inputs[0], inputs[2], sanitized)
            with self.assertRaisesRegex(routing_evidence.EvidenceError, "status does not replay"):
                self.publish(root / "bundle", tuple(inputs))

    def test_provenance_must_cover_exact_matrix_and_machines(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = self.make_inputs(Path(temporary))
            protocol, provenance = inputs[0], inputs[7]
            provenance["configurations"].pop()
            with self.assertRaisesRegex(routing_evidence.EvidenceError, "configurations"):
                routing_evidence.validate_provenance(provenance, protocol)

    def test_nonempty_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.make_inputs(root)
            output = root / "bundle"
            output.mkdir()
            (output / "owned.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(routing_evidence.EvidenceError, "must be empty"):
                self.publish(output, inputs)
            self.assertEqual("preserve", (output / "owned.txt").read_text())

    def test_untracked_file_and_dirty_revision_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.make_inputs(root)
            self.publish(root / "bundle", inputs)
            (root / "bundle" / "not-a-payload.txt").write_text("unexpected", encoding="utf-8")
            with self.assertRaisesRegex(routing_evidence.EvidenceError, "untracked"):
                routing_evidence.verify_bundle(root / "bundle")
            protocol, provenance = inputs[0], inputs[7]
            provenance["sourceRepository"]["dirty"] = True
            with self.assertRaisesRegex(routing_evidence.EvidenceError, "clean source revision"):
                routing_evidence.validate_provenance(provenance, protocol)


if __name__ == "__main__":
    unittest.main()
