import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routing_campaign  # noqa: E402
import routing_preflight  # noqa: E402
import routing_runner  # noqa: E402


class FakeGenerator:
    def __init__(self, *, observed_model=None, service_tier="priority", emit_runtime=True):
        self.observed_model = observed_model
        self.service_tier = service_tier
        self.emit_runtime = emit_runtime

    def generate(self, job, workspace, prompt, transcript_path):
        completed = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100, "cached_input_tokens": 20,
                "cache_write_input_tokens": 0, "output_tokens": 30,
                "reasoning_output_tokens": 10,
            },
        }
        if self.emit_runtime:
            completed.update({
                "model": self.observed_model or job["model"],
                "service_tier": self.service_tier,
            })
        transcript_path.write_text(
            json.dumps({"type": "thread.started"}) + "\n" +
            json.dumps({"type": "turn.started"}) + "\n" +
            json.dumps(completed) + "\n",
            encoding="utf-8",
        )
        return {
            "execution": {
                "exitCode": 0, "durationSeconds": 1.25, "stderr": "",
                "timedOut": False, "launcherFailure": False,
                "outputLimitExceeded": False,
            },
            "codexVersion": "codex-cli 0.147.0", "generatorImage": None,
            "backend": "docker", "isolation": "container-strong",
        }


class RoutingRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = routing_runner.load_runtime_manifest()
        frozen = json.loads((ROOT / "protocols" / "routing-v1.json").read_text(encoding="utf-8"))
        cls.protocol = copy.deepcopy(frozen)
        cls.protocol["protocolId"] = "routing-runner-test"
        cls.protocol["bootstrapSamples"] = 100
        cls.protocol["replicatesPerFixture"] = 3
        cls.protocol["machines"] = ["machine-a"]
        for family in cls.protocol["families"]:
            prefix = family["catalogFamilyId"]
            family["heldOutFixtureIds"] = [
                f"{prefix}-01", f"{prefix}-02", f"{prefix}-03", f"{prefix}-04"
            ]
            family["heldOutFixtureEcosystems"] = [
                "dotnet", "react-typescript", "python", "repository-artifacts"
            ]
        models = [{
            "id": model, "model": model,
            "supportedReasoningEfforts": [
                {"reasoningEffort": effort} for effort in ("low", "medium", "high")
            ],
            "serviceTiers": [{"id": "priority"}], "additionalSpeedTiers": ["fast"],
        } for model in sorted({item["model"] for item in cls.protocol["matrix"]})]
        image = {
            "tag": "test", "id": "sha256:" + "a" * 64, "repoDigests": [],
            "os": "linux", "architecture": "amd64", "specHash": "b" * 64,
        }
        cls.preflight = routing_preflight.make_report(
            cls.protocol, cls.runtime, models, image, cls.runtime["codexVersion"], "machine-a"
        )
        cls.plan = routing_campaign.make_plan(
            cls.protocol, b"0123456789abcdef", [cls.preflight], cls.runtime
        )

    def patched_task_functions(self):
        descriptor = {
            "manifestHash": "a" * 64, "adapter": "artifact-rubric-v1",
            "family": "mechanical",
        }

        def materialize(_task_id, destination):
            destination.mkdir(parents=True)
            (destination / "TASK.md").write_text("fixture", encoding="utf-8")
            return destination

        return mock.patch.multiple(
            routing_runner.routing_tasks,
            materialize=materialize,
            task_by_id=lambda _task_id: descriptor,
            load_template=lambda _task: {"evaluatorProfile": None},
        )

    def test_parse_transcript_extracts_usage_model_and_tier(self):
        raw = (
            b'{"type":"turn.completed","model":"gpt-5.6-luna",'
            b'"service_tier":"priority","usage":{"input_tokens":5,'
            b'"output_tokens":2,"total_tokens":7}}\n'
        )
        parsed = routing_runner.parse_transcript(raw)
        self.assertTrue(parsed["completed"])
        self.assertEqual("gpt-5.6-luna", parsed["observedModel"])
        self.assertEqual("priority", parsed["serviceTier"])
        self.assertEqual(5, parsed["usage"]["inputTokens"])
        self.assertEqual(7, parsed["usage"]["totalTokens"])

    def test_current_exec_jsonl_without_model_or_tier_is_request_verified(self):
        job = self.plan["jobs"][0]
        evaluator = lambda *_args, **_kwargs: {"status": "PASS", "candidateHash": "b" * 64}
        with tempfile.TemporaryDirectory() as temporary, self.patched_task_functions():
            result = routing_runner.run_job(
                self.protocol, self.plan, job["runId"],
                FakeGenerator(emit_runtime=False),
                machine_id=job["machineId"], run_root=Path(temporary) / "routing",
                evaluation_backend="native", evaluator=evaluator,
                runtime_manifest=self.runtime, preflight_report=self.preflight,
            )
        self.assertEqual("PASS", result["status"])
        self.assertIsNone(result["generation"]["observedModel"])
        self.assertIsNone(result["generation"]["serviceTier"])
        self.assertEqual("cli-request-and-success", result["generation"]["runtimeVerification"])
        self.assertEqual(130, result["usage"]["totalTokens"])
        self.assertEqual(0, result["usage"]["cacheWriteInputTokens"])

    def test_fake_run_emits_strict_v2_result_and_never_overwrites(self):
        job = self.plan["jobs"][0]
        evaluator = lambda *_args, **_kwargs: {"status": "PASS", "candidateHash": "b" * 64}
        with tempfile.TemporaryDirectory() as temporary, self.patched_task_functions():
            run_root = Path(temporary) / "routing"
            result = routing_runner.run_job(
                self.protocol, self.plan, job["runId"], FakeGenerator(),
                machine_id=job["machineId"], run_root=run_root,
                evaluation_backend="native", evaluator=evaluator,
                runtime_manifest=self.runtime, preflight_report=self.preflight,
            )
            self.assertEqual("PASS", result["status"])
            self.assertEqual("b" * 64, result["candidateHash"])
            self.assertEqual(routing_runner.value_hash(self.runtime), result["runtimeManifestHash"])
            self.assertEqual(100, result["usage"]["inputTokens"])
            self.assertTrue((run_root / "results" / f"{job['runId']}.json").exists())
            with self.assertRaises(FileExistsError):
                routing_runner.run_job(
                    self.protocol, self.plan, job["runId"], FakeGenerator(),
                    machine_id=job["machineId"], run_root=run_root,
                    evaluation_backend="native", evaluator=evaluator,
                    runtime_manifest=self.runtime, preflight_report=self.preflight,
                )

    def test_observed_runtime_drift_is_an_infrastructure_failure(self):
        job = self.plan["jobs"][0]
        evaluator = mock.Mock(return_value={"status": "PASS", "candidateHash": "b" * 64})
        with tempfile.TemporaryDirectory() as temporary, self.patched_task_functions():
            result = routing_runner.run_job(
                self.protocol, self.plan, job["runId"],
                FakeGenerator(service_tier="default"),
                machine_id=job["machineId"], run_root=Path(temporary) / "routing",
                evaluation_backend="native",
                evaluator=evaluator, runtime_manifest=self.runtime, preflight_report=self.preflight,
            )
        self.assertEqual("INFRA_FAILURE", result["status"])
        self.assertEqual("runtime-drift", result["failureKind"])
        evaluator.assert_not_called()

    def test_protocol_rejects_substituted_runtime_manifest(self):
        job = self.plan["jobs"][0]
        changed = copy.deepcopy(self.runtime)
        changed["serviceTier"] = "default"
        changed["advertisedCapabilities"]["serviceTier"] = "default"
        with tempfile.TemporaryDirectory() as temporary, self.patched_task_functions():
            with self.assertRaisesRegex(ValueError, "preregistered protocol hash"):
                routing_runner.run_job(
                    self.protocol, self.plan, job["runId"], FakeGenerator(),
                    machine_id=job["machineId"], run_root=Path(temporary) / "routing",
                    evaluation_backend="native",
                    runtime_manifest=changed, preflight_report=self.preflight,
                )

    def test_declared_machine_and_physical_order_are_enforced(self):
        first = self.plan["jobs"][0]
        second = next(
            item for item in self.plan["jobs"][1:]
            if item["machineId"] == first["machineId"]
        )
        with tempfile.TemporaryDirectory() as temporary, self.patched_task_functions():
            run_root = Path(temporary) / "routing"
            with self.assertRaisesRegex(ValueError, "assigned to"):
                routing_runner.run_job(
                    self.protocol, self.plan, first["runId"], FakeGenerator(),
                    machine_id="wrong-machine", run_root=run_root,
                    evaluation_backend="native", runtime_manifest=self.runtime, preflight_report=self.preflight,
                )
            with self.assertRaisesRegex(ValueError, "predecessor"):
                routing_runner.run_job(
                    self.protocol, self.plan, second["runId"], FakeGenerator(),
                    machine_id=second["machineId"], run_root=run_root,
                    evaluation_backend="native", runtime_manifest=self.runtime,
                    preflight_report=self.preflight,
                )

    def test_assigned_machine_preflight_must_match_frozen_plan(self):
        job = self.plan["jobs"][0]
        changed = copy.deepcopy(self.preflight)
        changed["generatorImage"]["architecture"] = "different"
        # Capability claims remain the same, but the per-machine report hash does not.
        with tempfile.TemporaryDirectory() as temporary, self.patched_task_functions():
            with self.assertRaisesRegex(ValueError, "plan binding"):
                routing_runner.run_job(
                    self.protocol, self.plan, job["runId"], FakeGenerator(),
                    machine_id=job["machineId"], run_root=Path(temporary) / "routing",
                    evaluation_backend="native", runtime_manifest=self.runtime,
                    preflight_report=changed,
                )


if __name__ == "__main__":
    unittest.main()
