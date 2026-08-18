import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coordinator_analysis  # noqa: E402
import coordinator_campaign  # noqa: E402
import coordinator_evidence  # noqa: E402
import coordinator_runner  # noqa: E402
import routing_policy  # noqa: E402
import routing_tasks  # noqa: E402


class CoordinatorEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = routing_tasks.load_catalog()
        cls.protocol = copy.deepcopy(coordinator_campaign.load_json(
            coordinator_campaign.DEFAULT_PROTOCOL))
        cls.protocol["bootstrapSamples"] = 1000
        cls.protocol["sourceBinding"]["catalogHash"] = coordinator_campaign.value_hash(
            cls.catalog)
        cls.plan = coordinator_campaign.make_plan(cls.protocol, b"0123456789abcdef")

    def results(self):
        commit = "1" * 40
        runner_hash = coordinator_evidence.sha256_file(Path(coordinator_runner.__file__))
        records = []
        for job in self.plan["jobs"]:
            candidate = job["coordinatorTreatmentId"] == self.protocol["analysis"]["candidateId"]
            comparator = job["coordinatorTreatmentId"] == self.protocol["analysis"]["decisionComparatorId"]
            passed = candidate or not comparator
            records.append({
                "schemaVersion": 1, "recordKind": "coordinator-result",
                "protocolHash": coordinator_campaign.value_hash(self.protocol),
                "planHash": coordinator_campaign.value_hash(self.plan),
                "runId": job["runId"], "fixtureId": job["fixtureId"],
                "replicate": job["replicate"], "machineId": job["machineId"],
                "orderPosition": job["orderPosition"],
                "coordinatorTreatmentId": job["coordinatorTreatmentId"],
                "coordinatorModel": job["coordinatorModel"],
                "coordinatorReasoningEffort": job["coordinatorReasoningEffort"],
                "workerPolicyHash": job["workerPolicyHash"],
                "status": "PASS" if passed else "CANDIDATE_FAILURE",
                "failureKind": None if passed else "integration-rejected",
                "promptHash": "a" * 64, "transcriptHash": "b" * 64,
                "generationDurationSeconds": 1.0,
                "usage": {"inputTokens": 1, "cachedInputTokens": 0,
                          "cacheWriteInputTokens": 0, "outputTokens": 1,
                          "reasoningOutputTokens": 0, "totalTokens": 2},
                "runtime": {"codexVersion": self.protocol["executionEnvelope"]["codexVersion"],
                            "observedCoordinatorModel": job["coordinatorModel"],
                            "serviceTier": self.protocol["executionEnvelope"]["serviceTier"],
                            "fastMode": True, "multiAgent": True},
                "delegations": [{
                    "workerSlot": slot, "agentId": f"agent-{slot}",
                    "promptHash": __import__("hashlib").sha256(
                        prompt.encode("utf-8")).hexdigest(),
                    "model": self.protocol["workerPolicy"]["model"],
                    "reasoningEffort": self.protocol["workerPolicy"]["reasoningEffort"],
                    "forkTurns": "none", "startedAtMs": slot * 10,
                    "completedAtMs": slot * 10 + 100, "status": "completed",
                    "nestedDelegations": 0, "artifactHash": str(slot) * 64,
                } for slot, prompt in enumerate(self.protocol["workerPolicy"]["prompts"], 1)],
                "coordination": {"traceCompliance": passed,
                                 "concurrencyCompliance": passed,
                                 "delegatedCount": 3, "completedCount": 3,
                                 "interventionCount": 0, "conflictsObserved": 0,
                                 "conflictsResolved": 0, "unresolvedConflicts": 0,
                                 "criticalPathSeconds": 1.0,
                                 "totalWorkerSeconds": 2.0, "utilization": 2 / 3},
                "integration": {"accepted": passed, "candidateHash": "c" * 64,
                                "reportHash": "d" * 64},
                "provenance": {"gitCommit": commit,
                               "files": {"coordinator_runner.py": runner_hash},
                               "platform": {"system": "test", "release": "test",
                                            "machine": "test", "python": "test"}},
            })
        return records

    def provenance(self):
        envelope = self.protocol["executionEnvelope"]
        worker = self.protocol["workerPolicy"]
        return {
            "schemaVersion": 1, "recordKind": "coordinator-provenance",
            "estimand": coordinator_evidence.ESTIMAND,
            "sourceRepository": {"url": "https://example.invalid/repo.git",
                                 "commit": "1" * 40, "dirty": False},
            "campaignRunner": {"name": "coordinator_runner.py", "version": "1",
                               "sha256": coordinator_evidence.sha256_file(
                                   Path(coordinator_runner.__file__))},
            "analysisImplementation": {"name": "coordinator_analysis.py", "version": "1",
                                       "sha256": coordinator_evidence.sha256_file(
                                           Path(coordinator_analysis.__file__))},
            "coordinatorTreatments": [{**item, "serviceTier": envelope["serviceTier"],
                                       "toolVersion": envelope["codexVersion"]}
                                      for item in self.protocol["coordinatorTreatments"]],
            "frozenWorkerPolicy": {"hash": coordinator_campaign.value_hash(worker),
                                   **{key: worker[key] for key in (
                                       "treatmentId", "model", "reasoningEffort")}},
            "machines": [{"id": item, "platform": "test"}
                         for item in self.protocol["machines"]],
        }

    def publish(self, root: Path):
        results = self.results()
        analysis = coordinator_analysis.analyze(
            self.protocol, self.plan, results, catalog=self.catalog)
        return coordinator_evidence.publish_bundle(
            root, protocol=self.protocol, plan=self.plan, catalog=self.catalog,
            results=results, input_hashes=["f" * 64], analysis=analysis,
            provenance=self.provenance(), fixture_root=ROOT / "fixtures",
            bundle_id="coordinator-test",
        )

    def test_publish_verify_and_replay_complete_coordinator_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            bundle = self.publish(root)
            self.assertEqual(coordinator_evidence.ESTIMAND, bundle["estimand"])
            self.assertEqual("SUPPORTED", bundle["decision"]["decision"])
            self.assertEqual(bundle, coordinator_evidence.verify_bundle(root))

    def test_tamper_and_worker_estimand_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bundle"
            self.publish(root)
            analysis_path = root / "analysis.json"
            analysis = json.loads(analysis_path.read_text())
            analysis["estimand"] = "worker"
            analysis_path.write_bytes(coordinator_evidence.canonical_bytes(analysis))
            with self.assertRaises(coordinator_evidence.EvidenceError):
                coordinator_evidence.verify_bundle(root)

    def test_policy_dispatches_strict_coordinator_estimand(self):
        policy = routing_policy.load_json(routing_policy.DEFAULT_ARTIFACT)
        route = policy["coordinatorDefaults"][0]
        route["claimStrength"] = "evidence-backed"
        policy["status"] = "mixed"
        comparators = [item["id"] for item in self.protocol["coordinatorTreatments"]
                       if item["id"] != "sol-medium"]
        reference = {"bundleId": "coordinator-test",
                     "estimand": coordinator_evidence.ESTIMAND,
                     "taskFamily": "coordination-integration",
                     "analysisId": self.protocol["protocolId"],
                    "configurationId": route["configurationId"],
                     "comparisonConfigurationIds": comparators,
                     "metric": "trace-and-integration-success-rate-difference",
                     "estimate": 1.0, "interval95": [1.0, 1.0],
                     "decision": "SUPPORTED"}
        route["evidenceRefs"] = [reference]
        analysis = {"schemaVersion": 1, "recordKind": "coordinator-analysis",
                    "estimand": coordinator_evidence.ESTIMAND, "complete": True,
                    "protocolId": self.protocol["protocolId"], "candidateId": "sol-medium",
                    "decision": "SUPPORTED", "comparisons": [
                        {"comparatorId": item} for item in comparators],
                    "decisionGate": {"metric": reference["metric"], "gain": 1.0,
                                     "lower95": 1.0, "upper95": 1.0}}
        bundle = {"schemaVersion": 1, "recordKind": "coordinator-evidence-bundle",
                  "estimand": coordinator_evidence.ESTIMAND,
                  "bundleId": "coordinator-test", "protocolHash": "1" * 64,
                  "planHash": "2" * 64,
                  "decision": {"taskFamily": "coordination-integration",
                               "candidateId": "sol-medium", "decision": "SUPPORTED"},
                  "artifacts": {"analysis": "analysis.json"}}
        policy["evidenceBundles"] = [{"id": "coordinator-test",
            "estimand": coordinator_evidence.ESTIMAND, "schemaVersion": 1,
            "canonicalSha256": routing_policy.canonical_sha256(bundle),
            "protocolHash": "1" * 64, "planHash": "2" * 64,
            "taskFamilies": ["coordination-integration"]}]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "analysis.json").write_text(json.dumps(analysis))
            with mock.patch.object(routing_policy.coordinator_evidence, "verify_bundle",
                                   return_value=bundle) as coordinator_verify, \
                    mock.patch.object(routing_policy.routing_evidence, "verify_bundle") as worker_verify:
                routing_policy.validate_policy(
                    policy, routing_policy.load_matrix(),
                    evidence_paths={"coordinator-test": path})
            coordinator_verify.assert_called_once()
            worker_verify.assert_not_called()

            worker_bundle = dict(bundle)
            worker_bundle["estimand"] = "worker"
            policy["evidenceBundles"][0]["canonicalSha256"] = (
                routing_policy.canonical_sha256(worker_bundle))
            with mock.patch.object(routing_policy.coordinator_evidence, "verify_bundle",
                                   return_value=worker_bundle), self.assertRaisesRegex(
                                       ValueError, "estimand"):
                routing_policy.validate_policy(
                    policy, routing_policy.load_matrix(),
                    evidence_paths={"coordinator-test": path})


if __name__ == "__main__":
    unittest.main()
