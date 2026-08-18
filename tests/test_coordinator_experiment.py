import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import coordinator_campaign as campaign  # noqa: E402
import coordinator_analysis  # noqa: E402
import coordinator_runner as runner  # noqa: E402


class FakeGenerator:
    def __init__(self, protocol, *, wrong_prompt=False):
        self.protocol = protocol
        self.wrong_prompt = wrong_prompt

    def generate(self, job, workspace, prompt, transcript_path):
        events = [{"type": "thread.started"}]
        for slot, worker_prompt in enumerate(self.protocol["workerPolicy"]["prompts"], 1):
            events.append({
                "type": "collaboration.spawn", "agentId": f"agent-{slot}",
                "message": worker_prompt + (" drift" if self.wrong_prompt and slot == 1 else ""),
                "model": self.protocol["workerPolicy"]["model"],
                "reasoningEffort": self.protocol["workerPolicy"]["reasoningEffort"],
                "forkTurns": "none",
                "nestedDelegations": 0,
            })
        events.append({"type": "collaboration.conflict", "resolved": True})
        events.append({"type": "collaboration.intervention"})
        for slot in range(1, 4):
            events.append({"type": "collaboration.worker.completed", "agentId": f"agent-{slot}",
                           "artifactHash": str(slot) * 64})
        events.append({
            "type": "turn.completed", "model": job["coordinatorModel"],
            "service_tier": self.protocol["executionEnvelope"]["serviceTier"],
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        })
        transcript_path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
        return {
            "execution": {"exitCode": 0, "durationSeconds": 2.0, "timedOut": False,
                          "launcherFailure": False, "outputLimitExceeded": False},
            "eventTimesMs": [0, 10, 20, 30, 35, 40, 110, 220, 330, 350],
            "codexVersion": self.protocol["executionEnvelope"]["codexVersion"],
        }


class CoordinatorExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = campaign.load_json(campaign.DEFAULT_PROTOCOL)
        cls.catalog = {"schemaVersion": 2, "tasks": [
            {"id": fixture, "family": "coordination-integration", "kind": "confirmatory",
             "development": False, "ecosystem": ("dotnet", "react", "python")[index % 3],
             "manifestHash": cls.protocol["sourceBinding"]["fixtureManifestHashes"][fixture]}
            for index, fixture in enumerate(cls.protocol["fixtureIds"])
        ]}
        cls.protocol = copy.deepcopy(cls.protocol)
        cls.protocol["sourceBinding"]["catalogHash"] = campaign.value_hash(cls.catalog)
        campaign.validate_protocol(cls.protocol, catalog=cls.catalog)
        cls.plan = campaign.make_plan(cls.protocol, b"0123456789abcdef")

    def test_complete_deterministic_balanced_plan(self):
        again = campaign.make_plan(self.protocol, b"0123456789abcdef")
        self.assertEqual(self.plan, again)
        self.assertEqual(648, len(self.plan["jobs"]))
        self.assertEqual(1, len({job["workerPolicyHash"] for job in self.plan["jobs"]}))
        by_machine_treatment = {}
        for job in self.plan["jobs"]:
            key = (job["machineId"], job["coordinatorTreatmentId"])
            by_machine_treatment[key] = by_machine_treatment.get(key, 0) + 1
        self.assertEqual({36}, set(by_machine_treatment.values()))

    def test_plan_rejects_worker_policy_tamper(self):
        changed = copy.deepcopy(self.protocol)
        changed["workerPolicy"]["reasoningEffort"] = "medium"
        with self.assertRaises(campaign.ValidationError):
            campaign.validate_plan(self.plan, changed)

    def patches(self):
        return mock.patch.multiple(
            runner.routing_tasks,
            load_catalog=lambda: self.catalog,
            materialize=lambda _id, destination: destination.mkdir(parents=True),
        )

    def test_fake_run_records_live_delegation_and_integration(self):
        job = next(item for item in self.plan["jobs"] if item["orderPosition"] == 1)
        evaluator = lambda *_args, **_kwargs: {"status": "PASS", "candidateHash": "a" * 64}
        with tempfile.TemporaryDirectory() as temporary, self.patches():
            result = runner.run_job(
                self.protocol, self.plan, job["runId"], FakeGenerator(self.protocol),
                machine_id=job["machineId"], run_root=Path(temporary),
                evaluator=evaluator, evaluation_backend="native",
            )
        self.assertEqual("PASS", result["status"])
        self.assertTrue(result["coordination"]["traceCompliance"])
        self.assertEqual(3, result["coordination"]["delegatedCount"])
        self.assertTrue(result["coordination"]["concurrencyCompliance"])
        self.assertEqual(1, result["coordination"]["interventionCount"])
        self.assertEqual(0, result["coordination"]["unresolvedConflicts"])
        self.assertGreater(result["coordination"]["criticalPathSeconds"], 0)
        self.assertTrue(result["integration"]["accepted"])

    def test_fake_run_fails_closed_on_prompt_drift(self):
        job = next(item for item in self.plan["jobs"] if item["orderPosition"] == 1)
        evaluator = lambda *_args, **_kwargs: {"status": "PASS", "candidateHash": "a" * 64}
        with tempfile.TemporaryDirectory() as temporary, self.patches():
            result = runner.run_job(
                self.protocol, self.plan, job["runId"], FakeGenerator(self.protocol, wrong_prompt=True),
                machine_id=job["machineId"], run_root=Path(temporary),
                evaluator=evaluator, evaluation_backend="native",
            )
        self.assertEqual("CANDIDATE_FAILURE", result["status"])
        self.assertEqual("trace-contract", result["failureKind"])

    def test_worker_prompt_is_invariant_across_coordinator_treatments(self):
        hashes = set()
        for treatment in self.protocol["coordinatorTreatments"]:
            changed = copy.deepcopy(self.protocol)
            changed["coordinatorTreatments"] = [treatment, self.protocol["coordinatorTreatments"][0]]
            if changed["coordinatorTreatments"][0] == changed["coordinatorTreatments"][1]:
                changed["coordinatorTreatments"][1] = self.protocol["coordinatorTreatments"][1]
            hashes.add(campaign.value_hash(changed["workerPolicy"]))
            self.assertIn(changed["workerPolicy"]["prompts"][0], runner.coordinator_prompt(changed))
        self.assertEqual(1, len(hashes))

    def test_trace_parser_does_not_infer_delegation_from_prose(self):
        raw = (json.dumps({"type": "assistant.message", "text":
                           "I spawned three workers and they all completed."}) + "\n").encode()
        parsed = runner.parse_trace(raw, [10], self.protocol)
        self.assertEqual([], parsed["delegations"])
        self.assertFalse(parsed["metrics"]["traceCompliance"])

    def test_trace_parser_understands_nested_tool_call_shape(self):
        prompt = self.protocol["workerPolicy"]["prompts"][0]
        spawn = {"type": "item.completed", "item": {
            "name": "spawn_agent",
            "arguments": json.dumps({"message": prompt, "model": "gpt-5.6-luna",
                                     "reasoning_effort": "high", "fork_turns": "none",
                                     "nestedDelegations": 0}),
            "result": {"agent_id": "worker-1"},
        }}
        complete = {"type": "collaboration.worker.completed", "agentId": "worker-1"}
        raw = (json.dumps(spawn) + "\n" + json.dumps(complete) + "\n").encode()
        parsed = runner.parse_trace(raw, [10, 110], self.protocol)
        self.assertEqual("worker-1", parsed["delegations"][0]["agentId"])
        self.assertEqual("completed", parsed["delegations"][0]["status"])

    def synthetic_results(self, protocol, plan, *, candidate_pass=True):
        prompt_hash = "a" * 64
        results = []
        for job in plan["jobs"]:
            is_candidate = job["coordinatorTreatmentId"] == protocol["analysis"]["candidateId"]
            is_decision_comparator = job["coordinatorTreatmentId"] == protocol["analysis"]["decisionComparatorId"]
            passed = candidate_pass if is_candidate else not is_decision_comparator
            result = {
                "schemaVersion": 1, "recordKind": "coordinator-result",
                "protocolHash": campaign.value_hash(protocol), "planHash": campaign.value_hash(plan),
                "runId": job["runId"], "fixtureId": job["fixtureId"], "replicate": job["replicate"],
                "machineId": job["machineId"], "orderPosition": job["orderPosition"],
                "coordinatorTreatmentId": job["coordinatorTreatmentId"],
                "coordinatorModel": job["coordinatorModel"],
                "coordinatorReasoningEffort": job["coordinatorReasoningEffort"],
                "workerPolicyHash": job["workerPolicyHash"],
                "status": "PASS" if passed else "CANDIDATE_FAILURE",
                "failureKind": None if passed else "integration-rejected",
                "promptHash": prompt_hash, "transcriptHash": "b" * 64,
                "generationDurationSeconds": 1.0,
                "usage": {"inputTokens": 1, "cachedInputTokens": 0,
                          "cacheWriteInputTokens": 0, "outputTokens": 1,
                          "reasoningOutputTokens": 0, "totalTokens": 2},
                "runtime": {"codexVersion": protocol["executionEnvelope"]["codexVersion"],
                            "observedCoordinatorModel": job["coordinatorModel"],
                            "serviceTier": protocol["executionEnvelope"]["serviceTier"],
                            "fastMode": True, "multiAgent": True},
                "delegations": [],
                "coordination": {"traceCompliance": passed, "concurrencyCompliance": passed,
                                 "delegatedCount": 3, "completedCount": 3,
                                 "interventionCount": 0, "conflictsObserved": 0,
                                 "conflictsResolved": 0, "unresolvedConflicts": 0,
                                 "criticalPathSeconds": 1.0, "totalWorkerSeconds": 2.0,
                                 "utilization": 2 / 3},
                "integration": {"accepted": passed,
                                "candidateHash": "c" * 64, "reportHash": "d" * 64},
                "provenance": {"gitCommit": None, "files": {"fixture": "e" * 64},
                               "platform": {"system": "test", "release": "test",
                                            "machine": "test", "python": "test"}},
            }
            results.append(result)
        return results

    def test_coordinator_analysis_supports_only_complete_preregistered_cohort(self):
        protocol = copy.deepcopy(self.protocol)
        protocol["bootstrapSamples"] = 1000
        plan = campaign.make_plan(protocol, b"0123456789abcdef")
        results = self.synthetic_results(protocol, plan)
        analysis = coordinator_analysis.analyze(protocol, plan, results, catalog=self.catalog)
        self.assertEqual("SUPPORTED", analysis["decision"])
        self.assertEqual("trace-and-integration-success", analysis["primaryMetric"])
        self.assertEqual(7, analysis["multiplicity"]["claimCount"])
        self.assertTrue(analysis["stability"]["passed"])
        with self.assertRaises(campaign.ValidationError):
            coordinator_analysis.analyze(protocol, plan, results[:-1], catalog=self.catalog)

    def test_coordinator_analysis_contradicts_failed_candidate(self):
        protocol = copy.deepcopy(self.protocol)
        protocol["bootstrapSamples"] = 1000
        plan = campaign.make_plan(protocol, b"0123456789abcdef")
        analysis = coordinator_analysis.analyze(
            protocol, plan, self.synthetic_results(protocol, plan, candidate_pass=False),
            catalog=self.catalog,
        )
        self.assertEqual("CONTRADICTED", analysis["decision"])


if __name__ == "__main__":
    unittest.main()
