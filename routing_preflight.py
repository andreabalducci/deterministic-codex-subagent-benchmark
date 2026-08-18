#!/usr/bin/env python3
"""Verify the authenticated Codex catalog before freezing a routing campaign."""
from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import harness
import routing_campaign
import routing_runner


ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "schemas" / "routing-preflight.schema.json"
REPORT_KEYS = {
    "schemaVersion", "recordKind", "complete", "machineId", "protocolHash",
    "runtimeManifestHash", "matrixHash", "catalogSource", "codexVersion",
    "serviceTier", "fastMode", "generatorImage", "treatments", "capabilityDigest",
}


class PreflightError(ValueError):
    pass


def validate_catalog(
    protocol: dict[str, Any], runtime: dict[str, Any], models: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for model in models:
        if not isinstance(model, dict):
            continue
        for key in (model.get("id"), model.get("model")):
            if isinstance(key, str) and key:
                by_name[key] = model
    checked: list[dict[str, Any]] = []
    for treatment in protocol["matrix"]:
        advertised = by_name.get(treatment["model"])
        if advertised is None:
            raise PreflightError(f"model is absent from authenticated catalog: {treatment['model']}")
        efforts = sorted({
            item.get("reasoningEffort") for item in advertised.get("supportedReasoningEfforts", [])
            if isinstance(item, dict) and isinstance(item.get("reasoningEffort"), str)
        })
        tiers = sorted({
            item.get("id") for item in advertised.get("serviceTiers", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        })
        legacy_speed = sorted({
            item for item in advertised.get("additionalSpeedTiers", [])
            if isinstance(item, str)
        })
        effort_ok = treatment["reasoningEffort"] in efforts
        tier_ok = (
            runtime["serviceTier"] in tiers
            or (runtime["serviceTier"] == "priority" and "fast" in legacy_speed)
        )
        if not effort_ok or not tier_ok:
            raise PreflightError(
                f"unsupported treatment {treatment['id']}: effort={effort_ok}, tier={tier_ok}"
            )
        checked.append({
            "id": treatment["id"], "model": treatment["model"],
            "reasoningEffort": treatment["reasoningEffort"],
            "catalogId": advertised.get("id"), "supportedReasoningEfforts": efforts,
            "serviceTiers": tiers, "additionalSpeedTiers": legacy_speed,
            "supported": True,
        })
    return checked


def query_catalog(auth_file: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    runtime = routing_runner.load_runtime_manifest()
    harness.ensure_image(
        routing_runner.ROUTING_GENERATOR_IMAGE,
        routing_runner.ROUTING_GENERATOR_DOCKERFILE,
    )
    with tempfile.TemporaryDirectory(prefix="routing-preflight-") as temporary:
        workspace = Path(temporary)
        container_name = f"codex-routing-preflight-{os.getpid()}"
        envelope = routing_runner.DockerCodexGenerator._container_arguments(
            workspace, container_name, harness.benchmark_auth_file(auth_file)
        )
        requests = [
            {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "routing-benchmark-preflight", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            }},
            {"method": "initialized", "params": {}},
            {"id": 2, "method": "model/list", "params": {
                "includeHidden": True, "limit": 100,
            }},
        ]
        command = [
            *envelope, routing_runner.ROUTING_GENERATOR_IMAGE,
            "codex", "app-server", "--stdio", "--strict-config",
            "--config", f'service_tier="{runtime["serviceTier"]}"',
            "--config", f'features.fast_mode={str(runtime["fastMode"]).lower()}',
            "--config", "features.multi_agent=false",
        ]
        process = subprocess.Popen(
            command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        try:
            assert process.stdin is not None and process.stdout is not None
            def send(item: dict[str, Any]) -> None:
                process.stdin.write(json.dumps(item, separators=(",", ":")) + "\n")
                process.stdin.flush()

            def receive(request_id: int, timeout: float = 30.0) -> dict[str, Any]:
                deadline = time.monotonic() + timeout
                observed_bytes = 0
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([process.stdout], [], [], 0.5)
                    if not ready:
                        if process.poll() is not None:
                            break
                        continue
                    line = process.stdout.readline()
                    observed_bytes += len(line.encode("utf-8"))
                    if observed_bytes > 4 * 1024 * 1024:
                        raise PreflightError("Codex app-server preflight exceeded output limit")
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and item.get("id") == request_id:
                        return item
                raise PreflightError(f"Codex app-server did not answer request {request_id}")

            send(requests[0])
            initialize = receive(1)
            if initialize.get("error") is not None:
                raise PreflightError("Codex app-server initialize failed")
            send(requests[1])
            send(requests[2])
            response = receive(2)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            subprocess.run(
                ["docker", "rm", "-f", container_name], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    if response is None or response.get("error") is not None:
        raise PreflightError("Codex app-server did not return model/list")
    models = response.get("result", {}).get("data")
    if not isinstance(models, list):
        raise PreflightError("Codex model/list response has no data array")
    return models, harness.docker_image_info(routing_runner.ROUTING_GENERATOR_IMAGE), runtime["codexVersion"]


def capability_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Return the machine-independent capability claim committed by a report."""
    return {
        "protocolHash": report["protocolHash"],
        "runtimeManifestHash": report["runtimeManifestHash"],
        "matrixHash": report["matrixHash"],
        "catalogSource": report["catalogSource"],
        "codexVersion": report["codexVersion"],
        "serviceTier": report["serviceTier"],
        "fastMode": report["fastMode"],
        "treatments": report["treatments"],
    }


def validate_report(
    report: Any, protocol: dict[str, Any], runtime: dict[str, Any],
    *, expected_machine_id: str | None = None,
) -> dict[str, Any]:
    if not isinstance(report, dict) or set(report) != REPORT_KEYS:
        raise PreflightError("preflight report has unsupported fields")
    try:
        harness.validate_schema_instance(
            report, json.loads(SCHEMA.read_text(encoding="utf-8"))
        )
    except ValueError as error:
        raise PreflightError("preflight report violates its strict schema") from error
    if report["machineId"] not in protocol["machines"]:
        raise PreflightError("preflight report names a machine outside the protocol")
    if expected_machine_id is not None and report["machineId"] != expected_machine_id:
        raise PreflightError("preflight report does not identify the assigned machine")
    if report["protocolHash"] != routing_campaign.value_hash(protocol):
        raise PreflightError("preflight report does not reverse-bind the protocol")
    if report["runtimeManifestHash"] != routing_campaign.value_hash(runtime) \
            or report["runtimeManifestHash"] != protocol["runtimeManifestHash"]:
        raise PreflightError("preflight report does not bind the runtime manifest")
    if report["matrixHash"] != routing_campaign.value_hash({"configurations": protocol["matrix"]}):
        raise PreflightError("preflight report does not bind the treatment matrix")
    if report["codexVersion"] != runtime["codexVersion"] \
            or report["serviceTier"] != runtime["serviceTier"] \
            or report["fastMode"] is not runtime["fastMode"]:
        raise PreflightError("preflight runtime controls differ from the frozen runtime")
    expected_treatments = validate_catalog(protocol, runtime, [{
        "id": item["catalogId"], "model": item["model"],
        "supportedReasoningEfforts": [
            {"reasoningEffort": effort} for effort in item["supportedReasoningEfforts"]
        ],
        "serviceTiers": [{"id": tier} for tier in item["serviceTiers"]],
        "additionalSpeedTiers": item["additionalSpeedTiers"],
    } for item in report["treatments"]])
    if report["treatments"] != expected_treatments:
        raise PreflightError("preflight treatment claims are non-canonical")
    if report["capabilityDigest"] != routing_campaign.value_hash(capability_payload(report)):
        raise PreflightError("preflight capability digest is invalid")
    return report


def make_report(
    protocol: dict[str, Any], runtime: dict[str, Any], models: list[dict[str, Any]],
    image: dict[str, Any], codex_version: str, machine_id: str,
) -> dict[str, Any]:
    if machine_id not in protocol["machines"]:
        raise PreflightError("machine ID is not registered in the protocol")
    checked = validate_catalog(protocol, runtime, models)
    report = {
        "schemaVersion": 2, "recordKind": "routing-capability-preflight",
        "complete": True,
        "machineId": machine_id,
        "protocolHash": routing_campaign.value_hash(protocol),
        "runtimeManifestHash": routing_campaign.value_hash(runtime),
        "matrixHash": routing_campaign.value_hash({"configurations": protocol["matrix"]}),
        "catalogSource": "app-server-model/list", "codexVersion": codex_version,
        "serviceTier": runtime["serviceTier"], "fastMode": runtime["fastMode"],
        "generatorImage": image, "treatments": checked,
    }
    report["capabilityDigest"] = routing_campaign.value_hash(capability_payload(report))
    return validate_report(report, protocol, runtime, expected_machine_id=machine_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=routing_campaign.DEFAULT_PROTOCOL)
    parser.add_argument("--runtime-manifest", type=Path, default=routing_runner.DEFAULT_RUNTIME_MANIFEST)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--machine-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    protocol = routing_campaign.validate_protocol(routing_campaign.load_json(args.protocol))
    runtime = routing_runner.load_runtime_manifest(args.runtime_manifest)
    if routing_campaign.value_hash(runtime) != protocol["runtimeManifestHash"]:
        raise PreflightError("runtime manifest is not bound to protocol")
    models, image, version = query_catalog(args.auth_file)
    report = make_report(protocol, runtime, models, image, version, args.machine_id)
    harness.save_json(args.output, report, replace=False)
    print(json.dumps({"valid": True, "treatments": len(report["treatments"]), "reportHash": routing_campaign.value_hash(report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
