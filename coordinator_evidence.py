#!/usr/bin/env python3
"""Publish and replay evidence for the live-coordinator experiment.

This bundle is deliberately incompatible with worker-routing evidence.  Its
estimand is a coordinator treatment while the leaf-worker policy is frozen by
the protocol and plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import coordinator_analysis
import coordinator_campaign
import coordinator_runner
import harness
import routing_tasks


ROOT = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = ROOT / "protocols" / "coordinator-v1.json"
DEFAULT_CATALOG = ROOT / "fixtures" / "catalog.json"
BUNDLE_SCHEMA = ROOT / "schemas" / "coordinator-evidence-bundle.schema.json"
AUDIT_SCHEMA = ROOT / "schemas" / "coordinator-evidence-audit.schema.json"
PROVENANCE_SCHEMA = ROOT / "schemas" / "coordinator-provenance.schema.json"
RESULT_SCHEMA = ROOT / "schemas" / "coordinator-result.schema.json"
ESTIMAND = "live-coordinator-with-frozen-workers"
SCHEMA_VERSION = 1
ARTIFACT_PATHS = {
    "protocol": "protocol.json",
    "plan": "plan.json",
    "catalog": "catalog.json",
    "manifestIndex": "manifest-index.json",
    "results": "results.json",
    "analysis": "analysis.json",
    "provenance": "provenance.json",
    "audit": "audit.json",
}


class EvidenceError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _exact(value: Any, keys: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = set(value) if isinstance(value, dict) else set()
        raise EvidenceError(f"{location} fields differ: {sorted(actual ^ keys)}")
    return value


def _hash(value: Any, location: str, *, git: bool = False) -> str:
    length = 40 if git else 64
    if not isinstance(value, str) or len(value) != length \
            or any(character not in "0123456789abcdef" for character in value):
        raise EvidenceError(f"{location} must be a lowercase {length}-character hash")
    return value


def _text(value: Any, location: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{location} must be a non-empty string")
    return value


def _safe_relative(value: Any, location: str) -> str:
    value = _text(value, location)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise EvidenceError(f"{location} must be a safe normalized relative path")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _read(path: Path) -> Any:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical_bytes(value):
        raise EvidenceError(f"Artifact is not canonical JSON: {path.name}")
    return value


def _load_results(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    hashes: list[str] = []
    for path in paths:
        raw = path.read_bytes()
        hashes.append(hashlib.sha256(raw).hexdigest())
        document = json.loads(raw)
        values = document if isinstance(document, list) else [document]
        if not all(isinstance(value, dict) for value in values):
            raise EvidenceError(f"Result document {path.name} contains a non-object")
        records.extend(values)
    if not hashes:
        raise EvidenceError("At least one result document is required")
    return records, sorted(hashes)


def validate_provenance(value: Any, protocol: dict[str, Any]) -> dict[str, Any]:
    try:
        harness.validate_schema_instance(value, json.loads(PROVENANCE_SCHEMA.read_text()))
    except ValueError as error:
        raise EvidenceError("Coordinator provenance violates its schema") from error
    repository = value["sourceRepository"]
    _hash(repository["commit"], "$provenance.sourceRepository.commit", git=True)
    if repository["dirty"] is not False:
        raise EvidenceError("Published coordinator evidence requires a clean source revision")
    expected_runner = sha256_file(Path(coordinator_runner.__file__))
    expected_analysis = sha256_file(Path(coordinator_analysis.__file__))
    if value["campaignRunner"] != {
        "name": "coordinator_runner.py", "version": "1", "sha256": expected_runner,
    }:
        raise EvidenceError("Campaign runner provenance does not identify coordinator_runner.py")
    if value["analysisImplementation"] != {
        "name": "coordinator_analysis.py", "version": "1", "sha256": expected_analysis,
    }:
        raise EvidenceError("Analysis provenance does not identify coordinator_analysis.py")
    envelope = protocol["executionEnvelope"]
    expected = {(item["id"], item["model"], item["reasoningEffort"],
                 envelope["serviceTier"], envelope["codexVersion"])
                for item in protocol["coordinatorTreatments"]}
    observed = {(item["id"], item["model"], item["reasoningEffort"],
                 item["serviceTier"], item["toolVersion"])
                for item in value["coordinatorTreatments"]}
    if observed != expected or len(observed) != len(value["coordinatorTreatments"]):
        raise EvidenceError("Coordinator treatment provenance does not match the protocol")
    worker = value["frozenWorkerPolicy"]
    if worker["hash"] != coordinator_campaign.value_hash(protocol["workerPolicy"]) \
            or {key: worker[key] for key in ("treatmentId", "model", "reasoningEffort")} != {
                key: protocol["workerPolicy"][key]
                for key in ("treatmentId", "model", "reasoningEffort")
            }:
        raise EvidenceError("Frozen worker provenance does not match the protocol")
    machines = {item["id"] for item in value["machines"]}
    if machines != set(protocol["machines"]) or len(machines) != len(value["machines"]):
        raise EvidenceError("Coordinator provenance machines do not match the protocol")
    return value


def validate_results(results: list[dict[str, Any]], protocol: dict[str, Any],
                     plan: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    for index, result in enumerate(results):
        try:
            harness.validate_schema_instance(result, schema)
            coordinator_runner.validate_result(result, protocol, plan)
        except (ValueError, TypeError, KeyError) as error:
            raise EvidenceError(f"Invalid coordinator result at index {index}") from error
        if result["status"] == "INFRA_FAILURE":
            raise EvidenceError("Published coordinator evidence requires a resolved cohort")
        envelope = protocol["executionEnvelope"]
        runtime = result["runtime"]
        if runtime["codexVersion"] != envelope["codexVersion"] \
                or runtime["serviceTier"] != envelope["serviceTier"] \
                or runtime["fastMode"] is not envelope["fastMode"] \
                or runtime["multiAgent"] is not True \
                or runtime["observedCoordinatorModel"] not in {
                    None, result["coordinatorModel"]
                }:
            raise EvidenceError(f"Result runtime controls drift for {result['runId']}")
        if result["coordination"]["traceCompliance"]:
            worker = protocol["workerPolicy"]
            expected_prompts = {
                hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                for prompt in worker["prompts"]
            }
            delegations = result["delegations"]
            observed_prompts = {item["promptHash"] for item in delegations}
            if len(delegations) != worker["workerCount"] \
                    or observed_prompts != expected_prompts \
                    or any(item["model"] != worker["model"]
                           or item["reasoningEffort"] != worker["reasoningEffort"]
                           or item["forkTurns"] != "none"
                           or item["nestedDelegations"] != 0
                           or item["status"] != "completed"
                           for item in delegations):
                raise EvidenceError(f"Result delegation trace drift for {result['runId']}")
        if result["status"] == "PASS" and (
            result["integration"]["candidateHash"] is None
            or result["integration"]["reportHash"] is None
        ):
            raise EvidenceError(f"Passing result lacks integration evidence for {result['runId']}")
        if result["provenance"]["gitCommit"] != provenance["sourceRepository"]["commit"]:
            raise EvidenceError(f"Result source revision mismatch for {result['runId']}")
        runner_hash = result["provenance"]["files"].get("coordinator_runner.py")
        if runner_hash != provenance["campaignRunner"]["sha256"]:
            raise EvidenceError(f"Result runner provenance mismatch for {result['runId']}")
    by_id = coordinator_analysis.validate_complete_results(results, protocol, plan)
    order = {job["runId"]: index for index, job in enumerate(plan["jobs"])}
    return sorted(by_id.values(), key=lambda item: order[item["runId"]])


def _manifests(catalog: dict[str, Any], protocol: dict[str, Any], fixture_root: Path) \
        -> list[dict[str, Any]]:
    tasks = {task["id"]: task for task in catalog["tasks"]}
    entries = []
    for fixture_id in sorted(protocol["fixtureIds"]):
        task = tasks[fixture_id]
        template = _safe_relative(task["template"], f"catalog.{fixture_id}.template")
        source = fixture_root / template / "fixture-manifest.json"
        manifest = json.loads(source.read_text(encoding="utf-8"))
        if manifest != {"schemaVersion": 1, "contentHash": task["manifestHash"]}:
            raise EvidenceError(f"Fixture manifest mismatch for {fixture_id}")
        entries.append({"fixtureId": fixture_id, "template": template,
                        "catalogManifestHash": task["manifestHash"],
                        "path": f"manifests/{fixture_id}.json", "manifest": manifest})
    return entries


def publish_bundle(output: Path, *, protocol: dict[str, Any], plan: dict[str, Any],
                   catalog: dict[str, Any], results: list[dict[str, Any]],
                   input_hashes: list[str], analysis: dict[str, Any],
                   provenance: dict[str, Any], fixture_root: Path,
                   bundle_id: str | None = None) -> dict[str, Any]:
    protocol = coordinator_campaign.validate_protocol(protocol, catalog=catalog)
    plan = coordinator_campaign.validate_plan(plan, protocol)
    provenance = validate_provenance(provenance, protocol)
    results = validate_results(results, protocol, plan, provenance)
    computed = coordinator_analysis.analyze(protocol, plan, results, catalog=catalog)
    coordinator_analysis.validate_analysis(analysis, protocol, plan)
    if canonical_bytes(computed) != canonical_bytes(analysis):
        raise EvidenceError("Published coordinator analysis does not replay")
    manifests = _manifests(catalog, protocol, fixture_root)
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise EvidenceError("Output directory must not exist or must be empty")
    output.mkdir(parents=True, exist_ok=True)
    documents = {
        "protocol.json": protocol,
        "plan.json": plan,
        "catalog.json": catalog,
        "manifest-index.json": [{key: item[key] for key in (
            "fixtureId", "template", "catalogManifestHash", "path"
        )} for item in manifests],
        "results.json": {"schemaVersion": 1,
                         "recordKind": "coordinator-evidence-results",
                         "estimand": ESTIMAND, "records": results},
        "analysis.json": analysis,
        "provenance.json": provenance,
    }
    for relative, document in documents.items():
        _write(output / relative, document)
    for item in manifests:
        _write(output / item["path"], item["manifest"])
    inventory = sorted(documents) + sorted(item["path"] for item in manifests)
    audit = {
        "schemaVersion": 1, "recordKind": "coordinator-evidence-audit",
        "estimand": ESTIMAND, "hashAlgorithm": "sha256",
        "artifacts": [{"path": relative, "sha256": sha256_file(output / relative),
                       "bytes": (output / relative).stat().st_size,
                       "mediaType": "application/json"}
                      for relative in sorted(inventory)],
        "inputs": {"resultDocumentHashes": sorted(input_hashes)},
        "replay": {"analysisAlgorithm": "coordinator_analysis.analyze",
                   "analysisImplementationSha256": sha256_file(Path(coordinator_analysis.__file__))},
    }
    _write(output / "audit.json", audit)
    core = {
        "protocolHash": coordinator_campaign.value_hash(protocol),
        "planHash": coordinator_campaign.value_hash(plan),
        "catalogHash": coordinator_campaign.value_hash(catalog),
        "workerPolicyHash": coordinator_campaign.value_hash(protocol["workerPolicy"]),
        "resultsHash": canonical_sha256(documents["results.json"]),
        "analysisHash": canonical_sha256(analysis),
        "provenanceHash": canonical_sha256(provenance),
        "auditHash": canonical_sha256(audit),
    }
    resolved_id = bundle_id or f"coordinator-evidence-{canonical_sha256(core)[:16]}"
    bundle = {
        "schemaVersion": SCHEMA_VERSION, "recordKind": "coordinator-evidence-bundle",
        "estimand": ESTIMAND, "bundleId": resolved_id,
        "protocolId": protocol["protocolId"], "complete": True, **core,
        "counts": {"fixtures": len(protocol["fixtureIds"]), "results": len(results),
                   "treatments": len(protocol["coordinatorTreatments"])},
        "decision": {"taskFamily": "coordination-integration",
                     "candidateId": analysis["candidateId"],
                     "decision": analysis["decision"]},
        "artifacts": dict(ARTIFACT_PATHS),
    }
    _write(output / "bundle.json", bundle)
    verify_bundle(output)
    return bundle


def verify_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir() or any(path.is_symlink() for path in root.rglob("*")):
        raise EvidenceError("Coordinator evidence must be a symlink-free directory")
    bundle = _read(root / "bundle.json")
    try:
        harness.validate_schema_instance(bundle, json.loads(BUNDLE_SCHEMA.read_text()))
    except ValueError as error:
        raise EvidenceError("Coordinator bundle index violates its schema") from error
    if bundle["estimand"] != ESTIMAND:
        raise EvidenceError("Coordinator bundle estimand differs")
    artifacts = {name: _read(root / relative) for name, relative in ARTIFACT_PATHS.items()}
    protocol = coordinator_campaign.validate_protocol(artifacts["protocol"],
                                                       catalog=artifacts["catalog"])
    plan = coordinator_campaign.validate_plan(artifacts["plan"], protocol)
    provenance = validate_provenance(artifacts["provenance"], protocol)
    results_document = _exact(artifacts["results"],
        {"schemaVersion", "recordKind", "estimand", "records"}, "$results")
    if results_document["schemaVersion"] != 1 \
            or results_document["recordKind"] != "coordinator-evidence-results" \
            or results_document["estimand"] != ESTIMAND:
        raise EvidenceError("Unsupported coordinator result cohort")
    results = validate_results(results_document["records"], protocol, plan, provenance)
    if results != results_document["records"]:
        raise EvidenceError("Coordinator results are not in canonical plan order")
    computed = coordinator_analysis.analyze(protocol, plan, results,
                                             catalog=artifacts["catalog"])
    if canonical_bytes(computed) != canonical_bytes(artifacts["analysis"]):
        raise EvidenceError("Coordinator analysis replay mismatch")
    # Verify the packaged index/docs directly; fixture source is not needed to replay analysis.
    index = artifacts["manifestIndex"]
    tasks = {task["id"]: task for task in artifacts["catalog"]["tasks"]}
    expected_index = []
    manifest_paths = []
    for fixture_id in sorted(protocol["fixtureIds"]):
        task = tasks[fixture_id]
        relative = f"manifests/{fixture_id}.json"
        manifest = _read(root / relative)
        if manifest != {"schemaVersion": 1, "contentHash": task["manifestHash"]}:
            raise EvidenceError(f"Packaged manifest mismatch for {fixture_id}")
        expected_index.append({"fixtureId": fixture_id, "template": task["template"],
                               "catalogManifestHash": task["manifestHash"], "path": relative})
        manifest_paths.append(relative)
    if index != expected_index:
        raise EvidenceError("Coordinator manifest index differs from the protocol fixtures")
    audit = artifacts["audit"]
    try:
        harness.validate_schema_instance(audit, json.loads(AUDIT_SCHEMA.read_text()))
    except ValueError as error:
        raise EvidenceError("Coordinator evidence audit violates its schema") from error
    input_hashes = audit["inputs"]["resultDocumentHashes"]
    if input_hashes != sorted(set(input_hashes)):
        raise EvidenceError("Coordinator input document hashes must be unique and sorted")
    expected_paths = sorted(list(set(ARTIFACT_PATHS.values()) - {"audit.json"}) + manifest_paths)
    if [item["path"] for item in audit["artifacts"]] != expected_paths:
        raise EvidenceError("Coordinator audit inventory differs")
    for item in audit["artifacts"]:
        relative = _safe_relative(item["path"], "$audit.artifact.path")
        path = root / relative
        if item["sha256"] != sha256_file(path) or item["bytes"] != path.stat().st_size:
            raise EvidenceError(f"Coordinator audit mismatch for {relative}")
    if audit["replay"] != {"analysisAlgorithm": "coordinator_analysis.analyze",
                            "analysisImplementationSha256": sha256_file(Path(coordinator_analysis.__file__))}:
        raise EvidenceError("Coordinator analysis replay implementation differs")
    inventory = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if inventory != set(expected_paths) | {"audit.json", "bundle.json"}:
        raise EvidenceError("Coordinator bundle contains missing or untracked files")
    expected_hashes = {
        "protocolHash": coordinator_campaign.value_hash(protocol),
        "planHash": coordinator_campaign.value_hash(plan),
        "catalogHash": coordinator_campaign.value_hash(artifacts["catalog"]),
        "workerPolicyHash": coordinator_campaign.value_hash(protocol["workerPolicy"]),
        "resultsHash": canonical_sha256(results_document),
        "analysisHash": canonical_sha256(artifacts["analysis"]),
        "provenanceHash": canonical_sha256(provenance),
        "auditHash": canonical_sha256(audit),
    }
    if any(bundle[key] != value for key, value in expected_hashes.items()):
        raise EvidenceError("Coordinator bundle hashes differ from packaged artifacts")
    expected_summary = {"taskFamily": "coordination-integration",
                        "candidateId": computed["candidateId"], "decision": computed["decision"]}
    expected_counts = {"fixtures": len(protocol["fixtureIds"]), "results": len(results),
                       "treatments": len(protocol["coordinatorTreatments"])}
    if bundle["protocolId"] != protocol["protocolId"] \
            or bundle["decision"] != expected_summary or bundle["counts"] != expected_counts:
        raise EvidenceError("Coordinator bundle summary differs from replayed evidence")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("results", nargs="+", type=Path)
    publish.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    publish.add_argument("--plan", required=True, type=Path)
    publish.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    publish.add_argument("--analysis", required=True, type=Path)
    publish.add_argument("--provenance", required=True, type=Path)
    publish.add_argument("--fixture-root", type=Path, default=ROOT / "fixtures")
    publish.add_argument("--bundle-id")
    publish.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    if args.command == "verify":
        bundle = verify_bundle(args.bundle)
    else:
        results, hashes = _load_results(args.results)
        bundle = publish_bundle(
            args.output, protocol=json.loads(args.protocol.read_text()),
            plan=json.loads(args.plan.read_text()), catalog=json.loads(args.catalog.read_text()),
            results=results, input_hashes=hashes,
            analysis=json.loads(args.analysis.read_text()),
            provenance=json.loads(args.provenance.read_text()),
            fixture_root=args.fixture_root, bundle_id=args.bundle_id,
        )
    print(json.dumps({"bundleId": bundle["bundleId"],
                      "canonicalSha256": canonical_sha256(bundle)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
