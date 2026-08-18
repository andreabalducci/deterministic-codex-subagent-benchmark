#!/usr/bin/env python3
"""Publish and independently replay cheapest-sufficient routing evidence."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable

import harness
import routing_campaign
import routing_evidence
import routing_sequential


ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1
BUNDLE_KIND = "routing-sequential-evidence-bundle"
AUDIT_KIND = "routing-sequential-evidence-audit"
BUNDLE_SCHEMA = ROOT / "schemas" / "routing-sequential-evidence-bundle.schema.json"
AUDIT_SCHEMA = ROOT / "schemas" / "routing-sequential-evidence-audit.schema.json"
EVALUATION_INPUTS_SCHEMA = ROOT / "schemas" / "routing-evaluation-inputs.schema.json"
ANALYSIS_SCHEMA = ROOT / "schemas" / "routing-sequential-analysis.schema.json"

ARTIFACT_PATHS = {
    "protocol": "protocol.json",
    "runtimeManifest": "runtime-manifest.json",
    "preflightReports": "preflight-reports.json",
    "plan": "plan.json",
    "matrix": "matrix.json",
    "catalog": "catalog.json",
    "manifestIndex": "manifest-index.json",
    "sequentialManifest": "sequential-manifest.json",
    "terminalState": "terminal-state.json",
    "results": "results.json",
    "evaluationInputs": "evaluation-inputs.json",
    "analysis": "analysis.json",
    "provenance": "provenance.json",
    "audit": "audit.json",
}


class SequentialEvidenceError(ValueError):
    """Raised when sequential evidence is incomplete or cannot be replayed."""


def _error(message: str, cause: Exception | None = None) -> SequentialEvidenceError:
    error = SequentialEvidenceError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def _load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(value: Any, path: Path, label: str) -> None:
    try:
        harness.validate_schema_instance(value, _load_schema(path))
    except ValueError as error:
        raise _error(f"{label} violates its strict schema", error)


def _read_canonical(path: Path) -> Any:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _error(f"Cannot read evidence artifact {path.name}", error)
    if raw != routing_evidence.canonical_bytes(value):
        raise SequentialEvidenceError(f"Artifact is not canonical JSON: {path.name}")
    return value


def _executed_ids(state: dict[str, Any]) -> list[str]:
    return sorted({
        item["runId"]
        for event in state["history"]
        for item in event["resultHashes"]
    })


def _validate_terminal_cohort(
    state: dict[str, Any], manifest: dict[str, Any], plan: dict[str, Any],
    protocol: dict[str, Any], results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if state.get("complete") is not True:
        raise SequentialEvidenceError("Sequential evidence requires a terminal state")
    by_run = routing_campaign.validate_results(results, plan, protocol)
    expected = set(_executed_ids(state))
    observed = set(by_run)
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    if missing:
        raise SequentialEvidenceError(f"Missing executed result {missing[0]}")
    if extra:
        raise SequentialEvidenceError(f"Unexecuted result is present {extra[0]}")
    if any(result["status"] == "INFRA_FAILURE" for result in results):
        raise SequentialEvidenceError("Sequential evidence cannot contain INFRA_FAILURE")
    routing_sequential.replay_state(state, manifest, plan, protocol, by_run)
    return by_run


def _replay_evaluation(
    fixture: Path, candidate: Path, *, evaluator_image: str,
) -> dict[str, Any]:
    """Replay a packaged evaluator, including the exact multi-file JSON adapter."""
    descriptor = json.loads((fixture / "task.json").read_text(encoding="utf-8"))
    if descriptor.get("adapter") != "json-semantic-diff-v1":
        return routing_evidence._replay_evaluation(
            fixture, candidate, evaluator_image=evaluator_image
        )
    routing_evidence._exact(
        descriptor,
        {"schemaVersion", "adapter", "mutable", "immutable", "required", "rubric"},
        "$packagedFixture.task",
    )
    expected_files = set(descriptor["mutable"]) | set(descriptor["immutable"]) \
        | set(descriptor["required"])
    for collection in (descriptor["mutable"], descriptor["immutable"], descriptor["required"]):
        if not isinstance(collection, list):
            raise SequentialEvidenceError("Packaged fixture file lists must be arrays")
        for relative in collection:
            routing_evidence._safe_relative(relative, "$packagedFixture.task.file")
    actual_files = {
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*") if path.is_file()
    }
    violations = [f"missing required file: {item}" for item in sorted(expected_files - actual_files)]
    violations += [f"unexpected candidate file: {item}" for item in sorted(actual_files - expected_files)]
    for relative in descriptor["immutable"]:
        if (candidate / relative).is_file() and routing_evidence.sha256_file(candidate / relative) \
                != routing_evidence.sha256_file(fixture / "starter" / relative):
            violations.append(f"modified immutable file: {relative}")
    report = {
        "candidateHash": None, "status": "FAIL", "integrityViolations": violations,
        "outcomes": [],
    }
    if violations:
        return report
    report["candidateHash"] = routing_evidence._candidate_hash(descriptor, candidate)
    outcomes = []
    for relative in descriptor["mutable"]:
        try:
            actual = json.loads((candidate / relative).read_text(encoding="utf-8"))
            expected = json.loads((fixture / "sealed" / relative).read_text(encoding="utf-8"))
            passed = actual == expected
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            passed = False
        outcomes.append({
            "id": f"exact-json-state:{relative}",
            "outcome": "PASS" if passed else "FAIL",
        })
    report["outcomes"] = outcomes
    report["status"] = "PASS" if outcomes and all(
        item["outcome"] == "PASS" for item in outcomes
    ) else "FAIL"
    return report


def _evaluation_inputs(
    results: list[dict[str, Any]], catalog: dict[str, Any], fixture_root: Path,
    candidate_root: Path, evaluator_image: str,
) -> dict[str, Any]:
    tasks = {task["id"]: task for task in catalog["tasks"]}
    fixtures = []
    for task in sorted(catalog["tasks"], key=lambda item: item["id"]):
        root = fixture_root / routing_evidence._safe_relative(
            task["template"], f"task {task['id']}.template"
        )
        fixtures.append({
            "fixtureId": task["id"], "manifestHash": task["manifestHash"],
            "files": routing_evidence._encoded_files(
                root, routing_evidence._packaged_fixture_files(root)
            ),
        })
    candidates = []
    for result in results:
        task = tasks[result["fixtureId"]]
        workspace = candidate_root / result["runId"]
        spec = json.loads(
            (fixture_root / task["template"] / "task.json").read_text(encoding="utf-8")
        )
        allowed = sorted(set(spec["mutable"]) | set(spec["immutable"]) | set(spec["required"]))
        replay = _replay_evaluation(
            fixture_root / task["template"], workspace, evaluator_image=evaluator_image
        )
        if replay["candidateHash"] != result["candidateHash"]:
            raise SequentialEvidenceError(f"Candidate/evaluation replay mismatch for {result['runId']}")
        expected_status = "PASS" if replay["status"] == "PASS" else "CANDIDATE_FAILURE"
        if result["status"] != expected_status:
            raise SequentialEvidenceError(f"Result status does not replay for {result['runId']}")
        candidates.append({
            "runId": result["runId"], "candidateHash": result["candidateHash"],
            "files": routing_evidence._encoded_files(workspace, allowed),
        })
    return {
        "schemaVersion": 1, "recordKind": "routing-evaluation-inputs",
        "evaluatorImage": evaluator_image, "fixtures": fixtures, "candidates": candidates,
    }


def _replay_evaluation_inputs(
    inputs: dict[str, Any], results: list[dict[str, Any]], catalog: dict[str, Any],
) -> None:
    _validate_schema(inputs, EVALUATION_INPUTS_SCHEMA, "Evaluation inputs")
    tasks = {task["id"]: task for task in catalog["tasks"]}
    fixtures = inputs["fixtures"]
    candidates = inputs["candidates"]
    fixture_by_id = {item.get("fixtureId"): item for item in fixtures}
    candidate_by_run = {item.get("runId"): item for item in candidates}
    if len(fixture_by_id) != len(fixtures) or set(fixture_by_id) != set(tasks):
        raise SequentialEvidenceError("Evaluation fixtures do not exactly cover the catalog")
    expected_runs = {result["runId"] for result in results}
    if len(candidate_by_run) != len(candidates) or set(candidate_by_run) != expected_runs:
        raise SequentialEvidenceError("Evaluation candidates do not exactly cover executed results")

    with tempfile.TemporaryDirectory() as temporary:
        replay_root = Path(temporary)
        fixture_paths: dict[str, Path] = {}
        for fixture_id, record in fixture_by_id.items():
            record = routing_evidence._exact(
                record, {"fixtureId", "manifestHash", "files"},
                f"$evaluationInputs.fixture.{fixture_id}",
            )
            task = tasks[fixture_id]
            if record["manifestHash"] != task["manifestHash"]:
                raise SequentialEvidenceError(f"Evaluation fixture hash mismatch for {fixture_id}")
            target = replay_root / "fixtures" / fixture_id
            routing_evidence._decode_files(record["files"], target, "fixture")
            fixture_paths[fixture_id] = target

        for result in results:
            run_id = result["runId"]
            record = routing_evidence._exact(
                candidate_by_run[run_id], {"runId", "candidateHash", "files"},
                f"$evaluationInputs.candidate.{run_id}",
            )
            if record["candidateHash"] != result["candidateHash"]:
                raise SequentialEvidenceError(f"Packaged candidate hash mismatch for {run_id}")
            target = replay_root / "candidates" / run_id
            routing_evidence._decode_files(record["files"], target, "candidate")
            replay = _replay_evaluation(
                fixture_paths[result["fixtureId"]], target,
                evaluator_image=inputs["evaluatorImage"],
            )
            expected_status = "PASS" if replay["status"] == "PASS" else "CANDIDATE_FAILURE"
            if replay["candidateHash"] != result["candidateHash"] \
                    or result["status"] != expected_status:
                raise SequentialEvidenceError(f"Evaluation replay mismatch for {run_id}")


def _validate_manifest_index(
    root: Path, index: Any, catalog: dict[str, Any], *, publication: bool = False,
) -> list[str]:
    entries = routing_evidence._manifest_entries(catalog, root) if publication else None
    if publication:
        return [item["path"] for item in entries or []]
    if not isinstance(index, list) or len(index) != len(catalog["tasks"]):
        raise SequentialEvidenceError("Manifest index does not cover the catalog")
    tasks = {task["id"]: task for task in catalog["tasks"]}
    paths: list[str] = []
    for position, raw in enumerate(index):
        entry = routing_evidence._exact(
            raw, {"fixtureId", "template", "catalogManifestHash", "path"},
            f"$manifestIndex[{position}]",
        )
        task = tasks.get(entry["fixtureId"])
        if task is None or entry["template"] != task["template"] \
                or entry["catalogManifestHash"] != task["manifestHash"]:
            raise SequentialEvidenceError("Manifest index does not match catalog")
        relative = routing_evidence._safe_relative(entry["path"], f"$manifestIndex[{position}].path")
        if relative != f"manifests/{entry['fixtureId']}.json":
            raise SequentialEvidenceError("Manifest path does not match fixture identity")
        document = _read_canonical(root / relative)
        if document != {"schemaVersion": 1, "contentHash": task["manifestHash"]}:
            raise SequentialEvidenceError("Packaged fixture manifest does not match catalog")
        paths.append(relative)
    return paths


def _audit_entries(output: Path, paths: Iterable[str]) -> list[dict[str, Any]]:
    return [{
        "path": relative,
        "sha256": routing_evidence.sha256_file(output / relative),
        "bytes": (output / relative).stat().st_size,
        "mediaType": "application/json",
    } for relative in sorted(paths)]


def publish_bundle(
    output: Path, *, protocol: dict[str, Any], runtime_manifest: dict[str, Any],
    preflight_reports: list[dict[str, Any]], plan: dict[str, Any], matrix: dict[str, Any],
    catalog: dict[str, Any], sequential_manifest: dict[str, Any],
    terminal_state: dict[str, Any], raw_results: list[dict[str, Any]],
    raw_document_hashes: list[str], analysis: dict[str, Any], provenance: dict[str, Any],
    fixture_root: Path, candidate_root: Path, bundle_id: str | None = None,
) -> dict[str, Any]:
    protocol = routing_campaign.validate_protocol(protocol)
    runtime_manifest = routing_evidence.validate_runtime_manifest(runtime_manifest, protocol)
    routing_campaign.validate_protocol_sources(protocol, matrix, catalog)
    bindings = routing_campaign.validate_preflight_reports(preflight_reports, protocol, runtime_manifest)
    plan = routing_campaign.validate_plan(plan, protocol)
    if plan["preflightBindings"] != bindings:
        raise SequentialEvidenceError("Plan bindings do not match supplied machine preflights")
    routing_sequential.validate_manifest(sequential_manifest, plan, protocol)
    provenance = routing_evidence.validate_provenance(provenance, protocol, runtime_manifest)
    implementation_hash = routing_evidence.sha256_file(Path(routing_sequential.__file__))
    if provenance["analysisImplementation"]["sha256"] != implementation_hash:
        raise SequentialEvidenceError(
            "provenance analysisImplementation hash does not identify routing_sequential.py"
        )
    results, discarded = routing_evidence.sanitize_results(raw_results, plan, protocol)
    by_run = _validate_terminal_cohort(
        terminal_state, sequential_manifest, plan, protocol, results
    )
    routing_evidence.validate_result_provenance(results, catalog, provenance)
    computed = routing_sequential.analyze_state(
        terminal_state, sequential_manifest, plan, protocol, by_run
    )
    _validate_schema(analysis, ANALYSIS_SCHEMA, "Sequential analysis")
    if routing_evidence.canonical_json(computed) != routing_evidence.canonical_json(analysis):
        raise SequentialEvidenceError("Sequential analysis does not replay from results")

    manifests = routing_evidence._manifest_entries(catalog, fixture_root)
    evaluation_inputs = _evaluation_inputs(
        results, catalog, fixture_root, candidate_root,
        provenance["evaluator"]["imageDigest"],
    )
    _validate_schema(evaluation_inputs, EVALUATION_INPUTS_SCHEMA, "Evaluation inputs")
    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise SequentialEvidenceError("Output directory must not exist or must be empty")
    output.mkdir(parents=True, exist_ok=True)
    documents = {
        "protocol.json": protocol,
        "runtime-manifest.json": runtime_manifest,
        "preflight-reports.json": {
            "schemaVersion": 1, "recordKind": "routing-preflight-reports",
            "reports": preflight_reports,
        },
        "plan.json": plan,
        "matrix.json": matrix,
        "catalog.json": catalog,
        "manifest-index.json": [{key: item[key] for key in (
            "fixtureId", "template", "catalogManifestHash", "path"
        )} for item in manifests],
        "sequential-manifest.json": sequential_manifest,
        "terminal-state.json": terminal_state,
        "results.json": {
            "schemaVersion": 1, "recordKind": "sanitized-routing-sequential-results",
            "records": results,
        },
        "evaluation-inputs.json": evaluation_inputs,
        "analysis.json": analysis,
        "provenance.json": provenance,
    }
    for relative, document in documents.items():
        routing_evidence._write_canonical(output / relative, document)
    for item in manifests:
        routing_evidence._write_canonical(output / item["path"], item["manifest"])

    payload_paths = sorted(documents) + sorted(item["path"] for item in manifests)
    input_hashes = sorted(set(
        routing_evidence._hash(item, "raw document hash") for item in raw_document_hashes
    ))
    if not input_hashes:
        raise SequentialEvidenceError("At least one raw result document hash is required")
    audit = {
        "schemaVersion": SCHEMA_VERSION,
        "recordKind": AUDIT_KIND,
        "hashAlgorithm": "sha256",
        "artifacts": _audit_entries(output, payload_paths),
        "sanitization": {
            "allowedResultFields": sorted(routing_evidence.publication_result_keys()),
            "discardedFields": discarded,
            "inputDocumentHashes": input_hashes,
        },
        "replay": {
            "estimand": "worker",
            "transitionAlgorithm": "routing_sequential.replay_state",
            "analysisAlgorithm": "routing_sequential.analyze_state",
            "sequentialImplementationSha256": implementation_hash,
            "evaluationAlgorithm": "routing_sequential_evidence._replay_evaluation",
            "evaluationImplementationSha256": routing_evidence.sha256_file(Path(routing_evidence.__file__)),
            "bundleImplementationSha256": routing_evidence.sha256_file(Path(__file__)),
        },
    }
    _validate_schema(audit, AUDIT_SCHEMA, "Sequential evidence audit")
    routing_evidence._write_canonical(output / "audit.json", audit)

    core = {
        "protocolHash": routing_campaign.value_hash(protocol),
        "runtimeManifestHash": routing_campaign.value_hash(runtime_manifest),
        "planHash": routing_campaign.value_hash(plan),
        "catalogHash": routing_campaign.value_hash(catalog),
        "sequentialManifestHash": routing_campaign.value_hash(sequential_manifest),
        "terminalStateHash": routing_campaign.value_hash(terminal_state),
        "resultsHash": routing_campaign.value_hash(documents["results.json"]),
        "analysisHash": routing_campaign.value_hash(analysis),
        "provenanceHash": routing_campaign.value_hash(provenance),
        "auditHash": routing_campaign.value_hash(audit),
    }
    resolved_id = bundle_id or (
        "routing-sequential-evidence-"
        + routing_evidence.sha256_bytes(routing_evidence.canonical_bytes(core))[:16]
    )
    routing_evidence._text(resolved_id, "$bundle.bundleId")
    bundle = {
        "schemaVersion": SCHEMA_VERSION,
        "recordKind": BUNDLE_KIND,
        "estimand": "worker",
        "bundleId": resolved_id,
        "protocolId": protocol["protocolId"],
        "complete": True,
        **core,
        "counts": {
            "families": len(protocol["families"]),
            "fixtures": len(manifests),
            "potentialResults": len(plan["jobs"]),
            "executedResults": len(results),
            "savedResults": len(plan["jobs"]) - len(results),
        },
        "decisions": [{
            "familyId": item["familyId"],
            "selectedTreatmentId": item["selectedTreatmentId"],
            "decision": item["decision"],
        } for item in analysis["families"]],
        "artifacts": dict(ARTIFACT_PATHS),
    }
    _validate_schema(bundle, BUNDLE_SCHEMA, "Sequential evidence bundle")
    routing_evidence._write_canonical(output / "bundle.json", bundle)
    verify_bundle(output)
    return bundle


def verify_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir() or any(path.is_symlink() for path in root.rglob("*")):
        raise SequentialEvidenceError("Evidence bundle must be a directory without symbolic links")
    bundle = _read_canonical(root / "bundle.json")
    _validate_schema(bundle, BUNDLE_SCHEMA, "Sequential evidence bundle")
    if bundle["complete"] is not True or bundle["artifacts"] != ARTIFACT_PATHS:
        raise SequentialEvidenceError("Unsupported or incomplete sequential evidence bundle")
    artifacts = {key: _read_canonical(root / relative) for key, relative in ARTIFACT_PATHS.items()}
    protocol = routing_campaign.validate_protocol(artifacts["protocol"])
    runtime_manifest = routing_evidence.validate_runtime_manifest(artifacts["runtimeManifest"], protocol)
    preflight_document = routing_evidence._exact(
        artifacts["preflightReports"], {"schemaVersion", "recordKind", "reports"},
        "$preflightReports",
    )
    if preflight_document["schemaVersion"] != 1 \
            or preflight_document["recordKind"] != "routing-preflight-reports":
        raise SequentialEvidenceError("Unsupported machine preflight collection")
    matrix, catalog = artifacts["matrix"], artifacts["catalog"]
    routing_campaign.validate_protocol_sources(protocol, matrix, catalog)
    bindings = routing_campaign.validate_preflight_reports(
        preflight_document["reports"], protocol, runtime_manifest
    )
    plan = routing_campaign.validate_plan(artifacts["plan"], protocol)
    if plan["preflightBindings"] != bindings:
        raise SequentialEvidenceError("Packaged machine preflights do not match plan bindings")
    manifest = routing_sequential.validate_manifest(
        artifacts["sequentialManifest"], plan, protocol
    )
    provenance = routing_evidence.validate_provenance(
        artifacts["provenance"], protocol, runtime_manifest
    )
    implementation_hash = routing_evidence.sha256_file(Path(routing_sequential.__file__))
    if provenance["analysisImplementation"]["sha256"] != implementation_hash:
        raise SequentialEvidenceError("Sequential implementation does not match provenance")

    results_document = routing_evidence._exact(
        artifacts["results"], {"schemaVersion", "recordKind", "records"}, "$results"
    )
    if results_document["schemaVersion"] != 1 \
            or results_document["recordKind"] != "sanitized-routing-sequential-results":
        raise SequentialEvidenceError("Unsupported sanitized sequential results")
    results, discarded = routing_evidence.sanitize_results(
        results_document["records"], plan, protocol
    )
    if discarded or results != results_document["records"]:
        raise SequentialEvidenceError("Published results are not the canonical sanitized cohort")
    by_run = _validate_terminal_cohort(
        artifacts["terminalState"], manifest, plan, protocol, results
    )
    routing_evidence.validate_result_provenance(results, catalog, provenance)
    inputs = artifacts["evaluationInputs"]
    if inputs.get("evaluatorImage") != provenance["evaluator"]["imageDigest"]:
        raise SequentialEvidenceError("Evaluation image does not match provenance")
    _replay_evaluation_inputs(inputs, results, catalog)
    computed = routing_sequential.analyze_state(
        artifacts["terminalState"], manifest, plan, protocol, by_run
    )
    _validate_schema(artifacts["analysis"], ANALYSIS_SCHEMA, "Sequential analysis")
    if routing_evidence.canonical_json(computed) != routing_evidence.canonical_json(artifacts["analysis"]):
        raise SequentialEvidenceError("Sequential analysis replay mismatch")

    manifest_paths = _validate_manifest_index(root, artifacts["manifestIndex"], catalog)
    audit = artifacts["audit"]
    _validate_schema(audit, AUDIT_SCHEMA, "Sequential evidence audit")
    expected_paths = sorted(
        list(set(ARTIFACT_PATHS.values()) - {"audit.json"}) + manifest_paths
    )
    if [entry["path"] for entry in audit["artifacts"]] != expected_paths:
        raise SequentialEvidenceError("Audit artifact inventory is incomplete or unordered")
    for entry in audit["artifacts"]:
        path = root / routing_evidence._safe_relative(entry["path"], "$audit.artifact.path")
        if entry["bytes"] != path.stat().st_size \
                or entry["sha256"] != routing_evidence.sha256_file(path):
            raise SequentialEvidenceError(f"Audit mismatch for {entry['path']}")
    observed = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if observed != set(expected_paths) | {"audit.json", "bundle.json"}:
        raise SequentialEvidenceError("Bundle contains missing or untracked files")
    sanitization = audit["sanitization"]
    if sanitization["allowedResultFields"] != sorted(routing_evidence.publication_result_keys()) \
            or not sanitization["inputDocumentHashes"]:
        raise SequentialEvidenceError("Invalid result sanitization audit")
    replay = audit["replay"]
    expected_replay = {
        "estimand": "worker",
        "transitionAlgorithm": "routing_sequential.replay_state",
        "analysisAlgorithm": "routing_sequential.analyze_state",
        "sequentialImplementationSha256": implementation_hash,
        "evaluationAlgorithm": "routing_sequential_evidence._replay_evaluation",
        "evaluationImplementationSha256": routing_evidence.sha256_file(Path(routing_evidence.__file__)),
        "bundleImplementationSha256": routing_evidence.sha256_file(Path(__file__)),
    }
    if replay != expected_replay:
        raise SequentialEvidenceError("Replay implementation identity mismatch")

    expected_hashes = {
        "protocolHash": routing_campaign.value_hash(protocol),
        "runtimeManifestHash": routing_campaign.value_hash(runtime_manifest),
        "planHash": routing_campaign.value_hash(plan),
        "catalogHash": routing_campaign.value_hash(catalog),
        "sequentialManifestHash": routing_campaign.value_hash(manifest),
        "terminalStateHash": routing_campaign.value_hash(artifacts["terminalState"]),
        "resultsHash": routing_campaign.value_hash(results_document),
        "analysisHash": routing_campaign.value_hash(artifacts["analysis"]),
        "provenanceHash": routing_campaign.value_hash(provenance),
        "auditHash": routing_campaign.value_hash(audit),
    }
    if any(bundle[field] != value for field, value in expected_hashes.items()):
        raise SequentialEvidenceError("Bundle index hashes do not match packaged artifacts")
    decisions = [{
        "familyId": item["familyId"],
        "selectedTreatmentId": item["selectedTreatmentId"],
        "decision": item["decision"],
    } for item in artifacts["analysis"]["families"]]
    counts = {
        "families": len(protocol["families"]),
        "fixtures": len(manifest_paths),
        "potentialResults": len(plan["jobs"]),
        "executedResults": len(results),
        "savedResults": len(plan["jobs"]) - len(results),
    }
    if bundle["protocolId"] != protocol["protocolId"] \
            or bundle["decisions"] != decisions or bundle["counts"] != counts:
        raise SequentialEvidenceError("Bundle summary does not match replayed evidence")
    return bundle


def _load_results(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    return routing_evidence._load_result_documents(paths)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("results", nargs="+", type=Path)
    publish.add_argument("--protocol", type=Path, required=True)
    publish.add_argument("--runtime-manifest", type=Path, required=True)
    publish.add_argument("--preflight", type=Path, action="append", required=True)
    publish.add_argument("--plan", type=Path, required=True)
    publish.add_argument("--matrix", type=Path, required=True)
    publish.add_argument("--catalog", type=Path, required=True)
    publish.add_argument("--sequential-manifest", type=Path, required=True)
    publish.add_argument("--terminal-state", type=Path, required=True)
    publish.add_argument("--analysis", type=Path, required=True)
    publish.add_argument("--provenance", type=Path, required=True)
    publish.add_argument("--fixture-root", type=Path, required=True)
    publish.add_argument("--candidate-root", type=Path, required=True)
    publish.add_argument("--bundle-id")
    publish.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "verify":
        bundle = verify_bundle(args.bundle)
        print(json.dumps({
            "valid": True,
            "bundleId": bundle["bundleId"],
            "bundleIndexCanonicalSha256": routing_campaign.value_hash(bundle),
            "decisions": {item["familyId"]: item["decision"] for item in bundle["decisions"]},
        }, indent=2, sort_keys=True))
        return 0
    raw_results, hashes = _load_results(args.results)
    load = lambda path: json.loads(path.read_text(encoding="utf-8"))
    bundle = publish_bundle(
        args.output,
        protocol=load(args.protocol), runtime_manifest=load(args.runtime_manifest),
        preflight_reports=[load(path) for path in args.preflight],
        plan=load(args.plan), matrix=load(args.matrix), catalog=load(args.catalog),
        sequential_manifest=load(args.sequential_manifest),
        terminal_state=load(args.terminal_state), raw_results=raw_results,
        raw_document_hashes=hashes, analysis=load(args.analysis),
        provenance=load(args.provenance), fixture_root=args.fixture_root,
        candidate_root=args.candidate_root, bundle_id=args.bundle_id,
    )
    print(json.dumps({
        "bundleId": bundle["bundleId"],
        "bundleIndexCanonicalSha256": routing_campaign.value_hash(bundle),
        "executedResults": bundle["counts"]["executedResults"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
