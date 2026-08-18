import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_campaign
import routing_preflight
import routing_runner
import routing_sequential


class SequentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = json.loads((ROOT / "protocols/routing-operational-v1.json").read_text())
        cls.runtime = routing_runner.load_runtime_manifest()
        models = [{
            "id": model, "model": model,
            "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("low", "medium", "high")],
            "serviceTiers": [{"id": "priority"}], "additionalSpeedTiers": ["fast"],
        } for model in sorted({item["model"] for item in cls.protocol["matrix"]})]
        image = {"tag": "test", "id": "sha256:" + "a" * 64, "repoDigests": [], "os": "linux", "architecture": "amd64", "specHash": "b" * 64}
        reports = [routing_preflight.make_report(cls.protocol, cls.runtime, models, image, cls.runtime["codexVersion"], machine) for machine in cls.protocol["machines"]]
        cls.plan = routing_campaign.make_plan(cls.protocol, b"0123456789abcdef", reports, cls.runtime)
        cls.manifest = routing_sequential.make_manifest(cls.plan, cls.protocol)

    def result(self, job, status="PASS"):
        return {
            "schemaVersion": 1, "recordKind": "routing-result",
            "protocolHash": routing_campaign.value_hash(self.protocol), "planHash": routing_campaign.value_hash(self.plan),
            "runId": job["runId"], "familyId": job["familyId"], "fixtureId": job["fixtureId"],
            "replicate": job["replicate"], "machineId": job["machineId"], "orderPosition": job["orderPosition"],
            "treatmentId": job["treatmentId"], "model": job["model"], "reasoningEffort": job["reasoningEffort"],
            "status": status, "generationDurationSeconds": 1.0, "evaluationDurationSeconds": 0.1,
        }

    def execute(self, targets):
        state = routing_sequential.make_initial_state(self.manifest, self.plan, self.protocol)
        by_id = {job["runId"]: job for job in self.plan["jobs"]}
        executed = {}
        while not state["complete"]:
            current = {}
            for authorization in state["authorized"]:
                target = targets[authorization["familyId"]]
                status = "PASS" if authorization["stageIndex"] >= target else "CANDIDATE_FAILURE"
                for run_id in authorization["runIds"]:
                    current[run_id] = self.result(by_id[run_id], status)
            executed.update(current)
            state = routing_sequential.advance_state(state, self.manifest, self.plan, self.protocol, {**executed})
        return state, executed

    def test_manifest_and_initial_state_are_deterministic(self):
        self.assertEqual(self.manifest, routing_sequential.make_manifest(self.plan, self.protocol))
        state = routing_sequential.make_initial_state(self.manifest, self.plan, self.protocol)
        self.assertEqual(108, sum(len(item["runIds"]) for item in state["authorized"]))
        self.assertEqual(state, routing_sequential.make_initial_state(self.manifest, self.plan, self.protocol))

    def test_best_case_executes_108_jobs_and_stops_at_stage_zero(self):
        targets = {family["id"]: 0 for family in self.protocol["families"]}
        state, executed = self.execute(targets)
        analysis = routing_sequential.analyze_state(
            state, self.manifest, self.plan, self.protocol, executed
        )
        self.assertTrue(analysis["complete"])
        self.assertEqual(108, analysis["executedJobs"])
        self.assertEqual(540, analysis["savedJobs"])
        self.assertTrue(all(item["decision"] == "ACCEPT" for item in analysis["families"]))
        self.assertEqual(108, len(executed))

    def test_hypothesized_ladder_executes_378_jobs(self):
        targets = {family["id"]: index for index, family in enumerate(self.protocol["families"])}
        state, executed = self.execute(targets)
        analysis = routing_sequential.analyze_state(
            state, self.manifest, self.plan, self.protocol, executed
        )
        self.assertEqual(378, analysis["executedJobs"])
        self.assertEqual(270, analysis["savedJobs"])
        self.assertEqual(378, len(executed))

    def test_worst_case_executes_all_648_jobs_and_exhausts(self):
        targets = {family["id"]: 6 for family in self.protocol["families"]}
        state, executed = self.execute(targets)
        analysis = routing_sequential.analyze_state(
            state, self.manifest, self.plan, self.protocol, executed
        )
        self.assertEqual(648, analysis["executedJobs"])
        self.assertEqual(0, analysis["savedJobs"])
        self.assertTrue(all(item["decision"] == "EXHAUSTED" for item in analysis["families"]))

    def test_missing_or_infrastructure_result_cannot_advance(self):
        state = routing_sequential.make_initial_state(self.manifest, self.plan, self.protocol)
        first = state["authorized"][0]["runIds"][0]
        jobs = {job["runId"]: job for job in self.plan["jobs"]}
        results = {run_id: self.result(jobs[run_id]) for item in state["authorized"] for run_id in item["runIds"]}
        with self.assertRaisesRegex(routing_sequential.SequentialError, "incomplete"):
            routing_sequential.advance_state(state, self.manifest, self.plan, self.protocol, {key: value for key, value in results.items() if key != first})
        results[first] = self.result(jobs[first], "INFRA_FAILURE")
        with self.assertRaisesRegex(routing_sequential.SequentialError, "infrastructure"):
            routing_sequential.advance_state(state, self.manifest, self.plan, self.protocol, results)

    def test_state_tampering_is_rejected(self):
        state = routing_sequential.make_initial_state(self.manifest, self.plan, self.protocol)
        changed = copy.deepcopy(state)
        changed["complete"] = True
        with self.assertRaisesRegex(routing_sequential.SequentialError, "state hash"):
            routing_sequential.validate_state(changed, self.manifest, self.plan, self.protocol)

    def test_self_consistent_metric_tampering_fails_result_replay(self):
        targets = {family["id"]: 0 for family in self.protocol["families"]}
        state, results = self.execute(targets)
        changed = copy.deepcopy(state)
        changed["history"][0]["metrics"][0]["accepted"] = False
        event = changed["history"][0]
        event["eventHash"] = routing_sequential.value_hash({
            key: value for key, value in event.items() if key != "eventHash"
        })
        changed["stateHash"] = routing_sequential.value_hash({
            key: value for key, value in changed.items() if key != "stateHash"
        })
        routing_sequential.validate_state(
            changed, self.manifest, self.plan, self.protocol
        )
        with self.assertRaisesRegex(
            routing_sequential.SequentialError, "does not replay"
        ):
            routing_sequential.analyze_state(
                changed, self.manifest, self.plan, self.protocol, results
            )


if __name__ == "__main__":
    unittest.main()
