#!/usr/bin/env python3
"""Publish and replay-verify portable routing evidence bundles.

Raw generator transcripts are never copied.  Candidate files are allow-listed by
the fixture contract, packaged with the sealed evaluator inputs, and replayed so a
consumer never has to trust an asserted PASS/FAIL status.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import routing_campaign
import routing_preflight
import routing_tasks
import harness


ROOT = Path(__file__).resolve().parent
DEFAULT_PROTOCOL = ROOT / "protocols" / "routing-v1.json"
DEFAULT_MATRIX = ROOT / "matrix.json"
DEFAULT_CATALOG = ROOT / "fixtures" / "catalog.json"
DEFAULT_RUNTIME_MANIFEST = ROOT / "protocols" / "routing-runtime-v1.json"
RESULT_SCHEMA = ROOT / "schemas" / "routing-result.schema.json"
EVALUATION_INPUTS_SCHEMA = ROOT / "schemas" / "routing-evaluation-inputs.schema.json"
SCHEMA_VERSION = 2
HASH_PATTERN_LENGTH = 64


class EvidenceError(ValueError):
    """Raised when an evidence package is incomplete or not reproducible."""


def publication_result_keys() -> set[str]:
    """All result fields safe and necessary for independent verification."""
    return set(routing_campaign.RESULT_KEYS) | set(
        getattr(routing_campaign, "RESULT_V2_KEYS", set())
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(value: Any) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise EvidenceError(f"{path} must contain exactly {sorted(keys)}; got {actual}")
    return value


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or len(value) != HASH_PATTERN_LENGTH \
            or any(character not in "0123456789abcdef" for character in value):
        raise EvidenceError(f"{path} must be a lowercase SHA-256 digest")
    return value


def _text(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{path} must be a non-empty string")
    return value


def _safe_relative(value: Any, path: str) -> str:
    _text(value, path)
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts \
            or "\\" in value or parsed.as_posix() != value:
        raise EvidenceError(f"{path} must be a normalized relative path")
    return value


def validate_runtime_manifest(value: Any, protocol: dict[str, Any]) -> dict[str, Any]:
    value = _exact(value, {
        "schemaVersion", "recordKind", "codexVersion", "serviceTier", "multiAgent",
        "fastMode", "ephemeral", "ignoreUserConfig", "ignoreRules", "advertisedCapabilities",
        "observability",
    }, "$runtimeManifest")
    if value["schemaVersion"] != 1 or value["recordKind"] != "routing-runtime-controls":
        raise EvidenceError("Unsupported runtime manifest")
    _text(value["codexVersion"], "$runtimeManifest.codexVersion")
    if value["serviceTier"] not in {"priority", "default"} \
            or value["fastMode"] is not (value["serviceTier"] == "priority") \
            or value["multiAgent"] is not False \
            or any(value[field] is not True for field in ("ephemeral", "ignoreUserConfig", "ignoreRules")):
        raise EvidenceError("Runtime controls violate the worker campaign contract")
    capabilities = _exact(
        value["advertisedCapabilities"],
        {"reasoningEfforts", "serviceTier", "treatmentIds"},
        "$runtimeManifest.advertisedCapabilities",
    )
    if capabilities["serviceTier"] != value["serviceTier"] \
            or capabilities["reasoningEfforts"] != ["low", "medium", "high"] \
            or capabilities["treatmentIds"] != [item["id"] for item in protocol["matrix"]]:
        raise EvidenceError("Runtime capabilities do not match the protocol matrix")
    if _exact(
        value["observability"], {"model", "serviceTier", "usage"},
        "$runtimeManifest.observability",
    ) != {"model": False, "serviceTier": False, "usage": True}:
        raise EvidenceError("Runtime telemetry contract does not match Codex exec JSONL")
    if routing_campaign.value_hash(value) != protocol["runtimeManifestHash"]:
        raise EvidenceError("Runtime manifest hash does not match the protocol")
    return value


def validate_provenance(
    value: Any, protocol: dict[str, Any], runtime_manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    value = _exact(
        value,
        {
            "schemaVersion", "recordKind", "sourceRepository", "campaignRunner",
            "analysisImplementation", "configurations", "evaluator", "machines",
        },
        "$provenance",
    )
    if value["schemaVersion"] != 1 or value["recordKind"] != "routing-provenance":
        raise EvidenceError("Unsupported provenance identity")
    repository = _exact(
        value["sourceRepository"], {"url", "commit", "dirty"},
        "$provenance.sourceRepository",
    )
    _text(repository["url"], "$provenance.sourceRepository.url", nullable=True)
    if not isinstance(repository["commit"], str) or len(repository["commit"]) != 40 \
            or any(character not in "0123456789abcdef" for character in repository["commit"]):
        raise EvidenceError("sourceRepository.commit must be a lowercase 40-character Git commit")
    if not isinstance(repository["dirty"], bool):
        raise EvidenceError("sourceRepository.dirty must be boolean")
    if repository["dirty"]:
        raise EvidenceError("Published routing evidence requires a clean source revision")
    for field in ("campaignRunner", "analysisImplementation"):
        item = _exact(value[field], {"name", "version", "sha256"}, f"$provenance.{field}")
        _text(item["name"], f"$provenance.{field}.name")
        _text(item["version"], f"$provenance.{field}.version")
        _hash(item["sha256"], f"$provenance.{field}.sha256")
    evaluator = _exact(
        value["evaluator"], {"name", "version", "imageDigest"},
        "$provenance.evaluator",
    )
    _text(evaluator["name"], "$provenance.evaluator.name")
    _text(evaluator["version"], "$provenance.evaluator.version")
    if not isinstance(evaluator["imageDigest"], str) \
            or not evaluator["imageDigest"].startswith("sha256:"):
        raise EvidenceError("evaluator.imageDigest must be a sha256: digest")
    _hash(evaluator["imageDigest"][7:], "$provenance.evaluator.imageDigest")

    expected_configurations = {
        (item["id"], item["model"], item["reasoningEffort"])
        for item in protocol["matrix"]
    }
    configurations: set[tuple[str, str, str]] = set()
    if not isinstance(value["configurations"], list):
        raise EvidenceError("provenance.configurations must be an array")
    for index, item in enumerate(value["configurations"]):
        item = _exact(
            item, {"id", "model", "reasoningEffort", "serviceTier", "toolVersion"},
            f"$provenance.configurations[{index}]",
        )
        for field in item:
            _text(item[field], f"$provenance.configurations[{index}].{field}")
        configurations.add((item["id"], item["model"], item["reasoningEffort"]))
    if configurations != expected_configurations or len(value["configurations"]) != len(configurations):
        raise EvidenceError("provenance configurations do not match the protocol matrix")
    if runtime_manifest is not None and any(
        item["serviceTier"] != runtime_manifest["serviceTier"]
        or item["toolVersion"] != runtime_manifest["codexVersion"]
        for item in value["configurations"]
    ):
        raise EvidenceError("provenance configurations do not match the runtime controls")

    expected_machines = set(protocol["machines"])
    observed_machines: set[str] = set()
    if not isinstance(value["machines"], list):
        raise EvidenceError("provenance.machines must be an array")
    for index, item in enumerate(value["machines"]):
        item = _exact(item, {"id", "platform"}, f"$provenance.machines[{index}]")
        _text(item["id"], f"$provenance.machines[{index}].id")
        _text(item["platform"], f"$provenance.machines[{index}].platform")
        observed_machines.add(item["id"])
    if observed_machines != expected_machines or len(value["machines"]) != len(observed_machines):
        raise EvidenceError("provenance machines do not match the protocol")
    return value


def _load_result_documents(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    document_hashes: list[str] = []
    for path in paths:
        raw = path.read_bytes()
        document_hashes.append(sha256_bytes(raw))
        if path.suffix == ".jsonl":
            values = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        else:
            document = json.loads(raw)
            values = document if isinstance(document, list) else [document]
        for value in values:
            if not isinstance(value, dict):
                raise EvidenceError(f"Result document {path.name} contains a non-object")
            records.append(value)
    return records, document_hashes


def sanitize_results(
    raw_results: list[dict[str, Any]], plan: dict[str, Any], protocol: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    all_allowed = publication_result_keys()
    sanitized: list[dict[str, Any]] = []
    discarded: set[str] = set()
    for index, result in enumerate(raw_results):
        version = result.get("schemaVersion")
        if version != 2:
            raise EvidenceError(
                "Published routing evidence requires provenance-rich schemaVersion 2 results"
            )
        required = (
            routing_campaign.RESULT_V2_KEYS
            if hasattr(routing_campaign, "RESULT_V2_KEYS") else routing_campaign.RESULT_KEYS
        )
        missing = required - set(result)
        if missing:
            raise EvidenceError(f"Result {index} is missing required fields: {sorted(missing)}")
        discarded.update(set(result) - all_allowed)
        sanitized.append({key: result[key] for key in sorted(required)})
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    for index, result in enumerate(sanitized):
        try:
            harness.validate_schema_instance(result, schema)
        except ValueError as error:
            raise EvidenceError(f"Published result {index} violates routing-result.schema.json") from error
    validated = routing_campaign.validate_results(sanitized, plan, protocol)
    plan_order = {job["runId"]: index for index, job in enumerate(plan["jobs"])}
    ordered = sorted(validated.values(), key=lambda item: plan_order[item["runId"]])
    return ordered, sorted(discarded)


def _image_digest(image: Any, path: str) -> str:
    if not isinstance(image, dict):
        raise EvidenceError(f"{path} must be a content-addressed container image")
    expected = {"tag", "id", "repoDigests", "os", "architecture", "specHash"}
    _exact(image, expected, path)
    _hash(image["specHash"], f"{path}.specHash")
    if not isinstance(image["id"], str) or not image["id"].startswith("sha256:"):
        raise EvidenceError(f"{path}.id must be content-addressed")
    image_id = _hash(image["id"][7:], f"{path}.id")
    digests = image["repoDigests"]
    if not isinstance(digests, list):
        raise EvidenceError(f"{path}.repoDigests must be an array")
    if not digests:
        return image_id
    parsed: set[str] = set()
    for index, value in enumerate(digests):
        if not isinstance(value, str) or "@sha256:" not in value:
            raise EvidenceError(f"{path}.repoDigests[{index}] is not content-addressed")
        digest = value.rsplit("@sha256:", 1)[1]
        _hash(digest, f"{path}.repoDigests[{index}]")
        parsed.add(digest)
    if len(parsed) != 1:
        raise EvidenceError(f"{path} must resolve to one image digest")
    return next(iter(parsed))


def validate_result_provenance(
    results: list[dict[str, Any]], catalog: dict[str, Any], provenance: dict[str, Any]
) -> None:
    tasks = {task.get("id"): task for task in catalog.get("tasks", []) if isinstance(task, dict)}
    source_commit = provenance["sourceRepository"]["commit"]
    evaluator_digest = provenance["evaluator"]["imageDigest"][7:]
    generator_digests: set[str] = set()
    observed_evaluator_digests: set[str] = set()
    for result in results:
        if result["status"] == "INFRA_FAILURE":
            raise EvidenceError(
                "Published evidence requires every planned run to reach candidate evaluation"
            )
        task = tasks.get(result["fixtureId"])
        if task is None or result["fixtureManifestHash"] != task.get("manifestHash"):
            raise EvidenceError(f"Result fixture manifest mismatch for {result['runId']}")
        if result["candidateHash"] is None or result["evaluation"]["reportHash"] is None:
            raise EvidenceError(f"Resolved result lacks candidate/evaluation evidence: {result['runId']}")
        if result["generation"]["backend"] != "docker" \
                or result["generation"]["isolation"] != "container-strong" \
                or result["evaluation"]["backend"] != "docker":
            raise EvidenceError(f"Campaign result is not strongly containerized: {result['runId']}")
        generator_digests.add(_image_digest(
            result["generation"]["generatorImage"],
            f"result {result['runId']}.generation.generatorImage",
        ))
        observed_evaluator_digests.add(_image_digest(
            result["evaluation"]["evaluatorImage"],
            f"result {result['runId']}.evaluation.evaluatorImage",
        ))
        if result["provenance"]["gitCommit"] != source_commit:
            raise EvidenceError(f"Result source revision mismatch for {result['runId']}")
    if len(generator_digests) != 1:
        raise EvidenceError("Campaign results use more than one generator image digest")
    if observed_evaluator_digests != {evaluator_digest}:
        raise EvidenceError("Campaign evaluator image does not match bundle provenance")


def _write_canonical(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def _manifest_entries(catalog: dict[str, Any], fixture_root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for task in sorted(catalog.get("tasks", []), key=lambda item: item.get("id", "")):
        for field in ("id", "template", "manifestHash"):
            if field not in task:
                raise EvidenceError(f"Catalog task is missing {field}")
        if not isinstance(task["id"], str) or not task["id"] \
                or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in task["id"]):
            raise EvidenceError("Catalog fixture IDs must be safe filename tokens")
        template = _safe_relative(task["template"], f"catalog task {task['id']}.template")
        _hash(task["manifestHash"], f"catalog task {task['id']}.manifestHash")
        source = fixture_root / template / "fixture-manifest.json"
        if not source.is_file():
            raise EvidenceError(f"Missing fixture manifest for {task['id']}")
        manifest = json.loads(source.read_text(encoding="utf-8"))
        _exact(manifest, {"schemaVersion", "contentHash"}, f"manifest {task['id']}")
        if manifest["schemaVersion"] != 1 or manifest["contentHash"] != task["manifestHash"]:
            raise EvidenceError(f"Fixture manifest does not match catalog for {task['id']}")
        path = f"manifests/{task['id']}.json"
        entries.append({
            "fixtureId": task["id"],
            "template": template,
            "catalogManifestHash": task["manifestHash"],
            "path": path,
            "manifest": manifest,
        })
    if not entries or len({entry["fixtureId"] for entry in entries}) != len(entries):
        raise EvidenceError("Catalog fixture IDs must be unique and non-empty")
    return entries


def _encoded_files(root: Path, relative_paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for relative in sorted(set(relative_paths)):
        normalized = _safe_relative(relative, "packaged file path")
        path = root / normalized
        if not path.is_file() or path.is_symlink():
            raise EvidenceError(f"Missing or unsafe packaged file: {normalized}")
        raw = path.read_bytes()
        files[normalized] = {
            "sha256": sha256_bytes(raw),
            "bytes": len(raw),
            "base64": base64.b64encode(raw).decode("ascii"),
        }
    return files


def _decode_files(files: Any, destination: Path, label: str) -> None:
    if not isinstance(files, dict) or not files:
        raise EvidenceError(f"{label} files must be a non-empty object")
    for relative, record in files.items():
        normalized = _safe_relative(relative, f"{label} file path")
        record = _exact(record, {"sha256", "bytes", "base64"}, f"{label}.{relative}")
        digest = _hash(record["sha256"], f"{label}.{relative}.sha256")
        if not isinstance(record["bytes"], int) or isinstance(record["bytes"], bool) \
                or record["bytes"] < 0 or not isinstance(record["base64"], str):
            raise EvidenceError(f"Invalid encoded file metadata for {label}.{relative}")
        try:
            raw = base64.b64decode(record["base64"], validate=True)
        except ValueError as error:
            raise EvidenceError(f"Invalid base64 for {label}.{relative}") from error
        if len(raw) != record["bytes"] or sha256_bytes(raw) != digest:
            raise EvidenceError(f"Encoded file hash mismatch for {label}.{relative}")
        path = destination / normalized
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def _packaged_fixture_files(root: Path) -> list[str]:
    paths = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in {"reference", "mutants"}:
            continue
        paths.append(relative.as_posix())
    return paths


def _candidate_hash(spec: dict[str, Any], candidate: Path) -> str:
    entries = {
        relative: sha256_file(candidate / relative)
        for relative in sorted(spec["mutable"])
    }
    return sha256_bytes(canonical_json(entries).encode("utf-8"))


def _replay_evaluation(
    fixture: Path, candidate: Path, *, evaluator_image: str
) -> dict[str, Any]:
    descriptor = json.loads((fixture / "task.json").read_text(encoding="utf-8"))
    expected_keys = {"schemaVersion", "adapter", "mutable", "immutable", "required", "rubric"}
    if descriptor.get("adapter") == "command-test-v1":
        expected_keys |= {"evaluatorProfile", "command"}
    _exact(descriptor, expected_keys, "$packagedFixture.task")
    for key in ("mutable", "immutable", "required"):
        if not isinstance(descriptor[key], list):
            raise EvidenceError(f"Packaged fixture {key} must be an array")
        for relative in descriptor[key]:
            _safe_relative(relative, f"$packagedFixture.task.{key}[]")
    expected_files = set(descriptor["mutable"]) | set(descriptor["immutable"]) \
        | set(descriptor["required"])
    actual_files = {
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*") if path.is_file()
    }
    violations = [f"missing required file: {item}" for item in sorted(expected_files - actual_files)]
    violations += [f"unexpected candidate file: {item}" for item in sorted(actual_files - expected_files)]
    for relative in descriptor["immutable"]:
        if (candidate / relative).is_file() \
                and sha256_file(candidate / relative) != sha256_file(fixture / "starter" / relative):
            violations.append(f"modified immutable file: {relative}")
    report: dict[str, Any] = {
        "schemaVersion": 2,
        "taskId": None,
        "family": None,
        "adapter": descriptor["adapter"],
        "candidateHash": None,
        "status": "FAIL",
        "integrityViolations": violations,
        "outcomes": [],
    }
    if violations:
        return report
    report["candidateHash"] = _candidate_hash(descriptor, candidate)
    sealed = json.loads((fixture / "sealed" / "expected.json").read_text(encoding="utf-8"))
    if descriptor["adapter"] == "exact-file-v1":
        relative = descriptor["mutable"][0]
        passed = (candidate / relative).read_bytes() == (fixture / "sealed" / relative).read_bytes()
        report["outcomes"] = [{"id": "exact-file", "outcome": "PASS" if passed else "FAIL"}]
    elif descriptor["adapter"] == "artifact-rubric-v1":
        relative = descriptor["mutable"][0]
        try:
            actual = json.loads((candidate / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            actual = None
        report["outcomes"] = [{
            "id": field,
            "outcome": "PASS" if isinstance(actual, dict) and actual.get(field) == sealed.get(field) else "FAIL",
        } for field in descriptor["rubric"]]
    elif descriptor["adapter"] == "command-test-v1":
        evaluator = json.loads((fixture / "sealed" / "evaluator.json").read_text(encoding="utf-8"))
        if set(evaluator) != {"schemaVersion", "adapter", "command"} \
                or evaluator["schemaVersion"] != 1 or evaluator["adapter"] != "sealed-command-v1":
            raise EvidenceError("Invalid packaged sealed command evaluator")
        command = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "1g", "--cpus", "2",
            *harness.docker_identity_arguments(),
            "--tmpfs", harness.docker_tmpfs("/tmp", "rw,noexec,nosuid,size=128m"),
            "--mount", f"type=bind,src={fixture.resolve()},dst=/fixture,readonly",
            "--mount", f"type=bind,src={candidate.resolve()},dst=/candidate,readonly",
            "--workdir", "/fixture", evaluator_image,
            *[item.replace("{candidate}", "/candidate") for item in evaluator["command"]],
        ]
        attempt = harness.execute_captured(
            command, cwd=ROOT, environment=None, timeout=90,
            limit=routing_tasks.MAX_COMMAND_OUTPUT_BYTES,
        )
        passed = attempt["exitCode"] == 0 and not attempt["timedOut"] \
            and not attempt["launcherFailure"] and not attempt["outputLimitExceeded"]
        report["outcomes"] = [{
            "id": "sealed-command", "outcome": "PASS" if passed else "FAIL", "attempt": attempt,
        }]
    else:
        raise EvidenceError(f"Unsupported packaged evaluator adapter: {descriptor['adapter']}")
    report["status"] = "PASS" if all(
        item["outcome"] == "PASS" for item in report["outcomes"]
    ) else "FAIL"
    return report


def _evaluation_inputs(
    results: list[dict[str, Any]], catalog: dict[str, Any], fixture_root: Path,
    candidate_root: Path, evaluator_image: str,
) -> dict[str, Any]:
    tasks = {task["id"]: task for task in catalog["tasks"]}
    fixtures = []
    for task in sorted(catalog["tasks"], key=lambda item: item["id"]):
        root = fixture_root / _safe_relative(task["template"], f"task {task['id']}.template")
        fixtures.append({
            "fixtureId": task["id"], "manifestHash": task["manifestHash"],
            "files": _encoded_files(root, _packaged_fixture_files(root)),
        })
    candidates = []
    for result in results:
        task = tasks[result["fixtureId"]]
        workspace = candidate_root / result["runId"]
        spec = json.loads((fixture_root / task["template"] / "task.json").read_text(encoding="utf-8"))
        allowed = sorted(set(spec["mutable"]) | set(spec["immutable"]) | set(spec["required"]))
        files = _encoded_files(workspace, allowed)
        replay = _replay_evaluation(
            fixture_root / task["template"], workspace, evaluator_image=evaluator_image
        )
        replay["taskId"], replay["family"] = result["fixtureId"], result["familyId"]
        if replay["candidateHash"] != result["candidateHash"]:
            raise EvidenceError(f"Candidate/evaluation replay mismatch for {result['runId']}")
        expected_status = "PASS" if replay["status"] == "PASS" else "CANDIDATE_FAILURE"
        if result["status"] != expected_status:
            raise EvidenceError(f"Result status does not replay for {result['runId']}")
        candidates.append({"runId": result["runId"], "candidateHash": result["candidateHash"], "files": files})
    return {
        "schemaVersion": 1, "recordKind": "routing-evaluation-inputs",
        "evaluatorImage": evaluator_image, "fixtures": fixtures, "candidates": candidates,
    }


ARTIFACT_PATHS = {
    "protocol": "protocol.json",
    "runtimeManifest": "runtime-manifest.json",
    "preflightReports": "preflight-reports.json",
    "plan": "plan.json",
    "matrix": "matrix.json",
    "catalog": "catalog.json",
    "manifestIndex": "manifest-index.json",
    "results": "results.json",
    "evaluationInputs": "evaluation-inputs.json",
    "analysis": "analysis.json",
    "provenance": "provenance.json",
    "audit": "audit.json",
}


def publish_bundle(
    output: Path,
    *,
    protocol: dict[str, Any],
    runtime_manifest: dict[str, Any],
    preflight_reports: list[dict[str, Any]],
    plan: dict[str, Any],
    matrix: dict[str, Any],
    catalog: dict[str, Any],
    raw_results: list[dict[str, Any]],
    raw_document_hashes: list[str],
    analysis: dict[str, Any],
    provenance: dict[str, Any],
    fixture_root: Path,
    candidate_root: Path,
    bundle_id: str | None = None,
) -> dict[str, Any]:
    protocol = routing_campaign.validate_protocol(protocol)
    runtime_manifest = validate_runtime_manifest(runtime_manifest, protocol)
    routing_campaign.validate_protocol_sources(protocol, matrix, catalog)
    bindings = routing_campaign.validate_preflight_reports(
        preflight_reports, protocol, runtime_manifest
    )
    plan = routing_campaign.validate_plan(plan, protocol)
    if plan["preflightBindings"] != bindings:
        raise EvidenceError("Plan bindings do not match supplied machine preflights")
    provenance = validate_provenance(provenance, protocol, runtime_manifest)
    implementation_hash = sha256_file(Path(routing_campaign.__file__))
    if provenance["analysisImplementation"]["sha256"] != implementation_hash:
        raise EvidenceError(
            "provenance analysisImplementation hash does not identify routing_campaign.py"
        )
    results, discarded = sanitize_results(raw_results, plan, protocol)
    validate_result_provenance(results, catalog, provenance)
    computed = routing_campaign.analyze(protocol, plan, results)
    routing_campaign.validate_analysis(analysis, protocol, plan)
    if canonical_json(computed) != canonical_json(analysis):
        raise EvidenceError("Published analysis does not replay from sanitized results")
    manifests = _manifest_entries(catalog, fixture_root)
    evaluator_image = provenance["evaluator"]["imageDigest"]
    evaluation_inputs = _evaluation_inputs(
        results, catalog, fixture_root, candidate_root, evaluator_image
    )
    harness.validate_schema_instance(
        evaluation_inputs,
        json.loads(EVALUATION_INPUTS_SCHEMA.read_text(encoding="utf-8")),
    )

    output = output.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise EvidenceError("Output directory must not exist or must be empty")
    output.mkdir(parents=True, exist_ok=True)
    documents = {
        "protocol.json": protocol,
        "runtime-manifest.json": runtime_manifest,
        "preflight-reports.json": {
            "schemaVersion": 1,
            "recordKind": "routing-preflight-reports",
            "reports": preflight_reports,
        },
        "plan.json": plan,
        "matrix.json": matrix,
        "catalog.json": catalog,
        "manifest-index.json": [{key: item[key] for key in (
            "fixtureId", "template", "catalogManifestHash", "path"
        )} for item in manifests],
        "results.json": {
            "schemaVersion": 1,
            "recordKind": "sanitized-routing-results",
            "records": results,
        },
        "evaluation-inputs.json": evaluation_inputs,
        "analysis.json": analysis,
        "provenance.json": provenance,
    }
    for relative, document in documents.items():
        _write_canonical(output / relative, document)
    for item in manifests:
        _write_canonical(output / item["path"], item["manifest"])

    payload_paths = sorted(documents) + sorted(item["path"] for item in manifests)
    audit_entries = []
    for relative in sorted(payload_paths):
        path = output / relative
        audit_entries.append({
            "path": relative,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "mediaType": "application/json",
        })
    audit = {
        "schemaVersion": 1,
        "recordKind": "routing-evidence-audit",
        "hashAlgorithm": "sha256",
        "artifacts": audit_entries,
        "sanitization": {
            "allowedResultFields": sorted(publication_result_keys()),
            "discardedFields": discarded,
            "inputDocumentHashes": sorted(_hash(item, "raw document hash") for item in raw_document_hashes),
        },
        "replay": {
            "estimand": "worker",
            "analysisAlgorithm": "routing_campaign.analyze",
            "analysisImplementationSha256": implementation_hash,
            "evaluationAlgorithm": "routing_evidence._replay_evaluation",
            "evaluationImplementationSha256": sha256_file(Path(__file__)),
        },
    }
    _write_canonical(output / "audit.json", audit)

    core = {
        "protocolHash": routing_campaign.value_hash(protocol),
        "runtimeManifestHash": routing_campaign.value_hash(runtime_manifest),
        "planHash": routing_campaign.value_hash(plan),
        "catalogHash": routing_campaign.value_hash(catalog),
        "resultsHash": routing_campaign.value_hash(documents["results.json"]),
        "analysisHash": routing_campaign.value_hash(analysis),
        "provenanceHash": routing_campaign.value_hash(provenance),
        "auditHash": routing_campaign.value_hash(audit),
    }
    resolved_id = bundle_id or f"routing-evidence-{sha256_bytes(canonical_bytes(core))[:16]}"
    _text(resolved_id, "$bundle.bundleId")
    bundle = {
        "schemaVersion": SCHEMA_VERSION,
        "recordKind": "routing-evidence-bundle",
        "estimand": "worker",
        "bundleId": resolved_id,
        "protocolId": protocol["protocolId"],
        "complete": True,
        **core,
        "counts": {
            "families": len(protocol["families"]),
            "fixtures": len(manifests),
            "results": len(results),
        },
        "decisions": [{
            "familyId": item["familyId"],
            "candidateId": item["candidateId"],
            "decision": item["decision"],
        } for item in analysis["families"]],
        "artifacts": dict(ARTIFACT_PATHS),
    }
    _write_canonical(output / "bundle.json", bundle)
    verify_bundle(output)
    return bundle


def _read_canonical(path: Path) -> Any:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical_bytes(value):
        raise EvidenceError(f"Artifact is not canonical JSON: {path.name}")
    return value


def validate_bundle_index(bundle: Any) -> dict[str, Any]:
    bundle = _exact(bundle, {
        "schemaVersion", "recordKind", "estimand", "bundleId", "protocolId", "complete",
        "protocolHash", "runtimeManifestHash", "planHash", "catalogHash", "resultsHash", "analysisHash",
        "provenanceHash", "auditHash", "counts", "decisions", "artifacts",
    }, "$bundle")
    if bundle["schemaVersion"] != SCHEMA_VERSION or bundle["recordKind"] != "routing-evidence-bundle" \
            or bundle["complete"] is not True:
        raise EvidenceError("Unsupported or incomplete evidence bundle")
    if bundle["estimand"] != "worker":
        raise EvidenceError("This publisher only supports worker-routing evidence")
    _text(bundle["bundleId"], "$bundle.bundleId")
    _text(bundle["protocolId"], "$bundle.protocolId")
    for field in (
        "protocolHash", "runtimeManifestHash", "planHash", "catalogHash", "resultsHash", "analysisHash",
        "provenanceHash", "auditHash",
    ):
        _hash(bundle[field], f"$bundle.{field}")
    counts = _exact(bundle["counts"], {"families", "fixtures", "results"}, "$bundle.counts")
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in counts.values()):
        raise EvidenceError("Bundle counts must be positive integers")
    if bundle["artifacts"] != ARTIFACT_PATHS:
        raise EvidenceError("Bundle artifact paths are not the frozen layout")
    if not isinstance(bundle["decisions"], list) or not bundle["decisions"]:
        raise EvidenceError("Bundle decisions must be a non-empty array")
    for index, decision in enumerate(bundle["decisions"]):
        _exact(decision, {"familyId", "candidateId", "decision"}, f"$bundle.decisions[{index}]")
        if decision["decision"] not in routing_campaign.ALLOWED_DECISIONS:
            raise EvidenceError("Bundle contains an invalid decision")
    return bundle


def verify_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise EvidenceError("Evidence bundle must be a directory")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise EvidenceError("Evidence bundles must not contain symbolic links")
    bundle = validate_bundle_index(_read_canonical(root / "bundle.json"))
    artifacts = {name: _read_canonical(root / relative) for name, relative in ARTIFACT_PATHS.items()}
    protocol = routing_campaign.validate_protocol(artifacts["protocol"])
    runtime_manifest = validate_runtime_manifest(artifacts["runtimeManifest"], protocol)
    preflight_document = _exact(
        artifacts["preflightReports"],
        {"schemaVersion", "recordKind", "reports"}, "$preflightReports",
    )
    if preflight_document["schemaVersion"] != 1 \
            or preflight_document["recordKind"] != "routing-preflight-reports" \
            or not isinstance(preflight_document["reports"], list):
        raise EvidenceError("Unsupported machine preflight collection")
    matrix, catalog = artifacts["matrix"], artifacts["catalog"]
    routing_campaign.validate_protocol_sources(protocol, matrix, catalog)
    expected_bindings = routing_campaign.validate_preflight_reports(
        preflight_document["reports"], protocol, runtime_manifest
    )
    plan = routing_campaign.validate_plan(artifacts["plan"], protocol)
    if plan["preflightBindings"] != expected_bindings:
        raise EvidenceError("Packaged machine preflights do not match plan bindings")
    validate_provenance(artifacts["provenance"], protocol, runtime_manifest)
    results_document = _exact(
        artifacts["results"], {"schemaVersion", "recordKind", "records"}, "$results"
    )
    if results_document["schemaVersion"] != 1 \
            or results_document["recordKind"] != "sanitized-routing-results" \
            or not isinstance(results_document["records"], list):
        raise EvidenceError("Unsupported sanitized result document")
    results, discarded = sanitize_results(results_document["records"], plan, protocol)
    if discarded or results != results_document["records"]:
        raise EvidenceError("Published results are not the canonical sanitized cohort")
    validate_result_provenance(results, catalog, artifacts["provenance"])
    evaluation_inputs = _exact(
        artifacts["evaluationInputs"],
        {"schemaVersion", "recordKind", "evaluatorImage", "fixtures", "candidates"},
        "$evaluationInputs",
    )
    try:
        harness.validate_schema_instance(
            evaluation_inputs,
            json.loads(EVALUATION_INPUTS_SCHEMA.read_text(encoding="utf-8")),
        )
    except ValueError as error:
        raise EvidenceError("Evaluation inputs violate their strict schema") from error
    if evaluation_inputs["schemaVersion"] != 1 \
            or evaluation_inputs["recordKind"] != "routing-evaluation-inputs" \
            or evaluation_inputs["evaluatorImage"] != artifacts["provenance"]["evaluator"]["imageDigest"]:
        raise EvidenceError("Unsupported evaluation replay inputs")
    packaged_fixtures = evaluation_inputs["fixtures"]
    packaged_candidates = evaluation_inputs["candidates"]
    if not isinstance(packaged_fixtures, list) or not isinstance(packaged_candidates, list):
        raise EvidenceError("Evaluation fixtures and candidates must be arrays")
    fixture_by_id = {item.get("fixtureId"): item for item in packaged_fixtures if isinstance(item, dict)}
    candidate_by_run = {item.get("runId"): item for item in packaged_candidates if isinstance(item, dict)}
    if len(fixture_by_id) != len(packaged_fixtures) or set(fixture_by_id) != {
        task["id"] for task in catalog["tasks"]
    }:
        raise EvidenceError("Evaluation fixtures do not exactly cover the catalog")
    expected_run_ids = {item["runId"] for item in results}
    if len(candidate_by_run) != len(packaged_candidates) or set(candidate_by_run) != expected_run_ids:
        raise EvidenceError("Evaluation candidates do not exactly cover resolved results")
    task_by_id = {task["id"]: task for task in catalog["tasks"]}
    with tempfile.TemporaryDirectory() as temporary:
        replay_root = Path(temporary)
        fixture_paths: dict[str, Path] = {}
        for fixture_id, fixture_record_unchecked in fixture_by_id.items():
            fixture_record = _exact(
                fixture_record_unchecked, {"fixtureId", "manifestHash", "files"},
                f"$evaluationInputs.fixture.{fixture_id}",
            )
            task = task_by_id[fixture_id]
            if fixture_record["manifestHash"] != task["manifestHash"]:
                raise EvidenceError(f"Evaluation fixture hash mismatch for {fixture_id}")
            fixture_copy = replay_root / "fixtures" / fixture_id
            _decode_files(fixture_record["files"], fixture_copy, "fixture")
            fixture_paths[fixture_id] = fixture_copy
        for result in results:
            candidate_record = _exact(
                candidate_by_run[result["runId"]], {"runId", "candidateHash", "files"},
                f"$evaluationInputs.candidate.{result['runId']}",
            )
            if candidate_record["candidateHash"] != result["candidateHash"]:
                raise EvidenceError(f"Packaged candidate hash mismatch for {result['runId']}")
            candidate_copy = replay_root / "candidates" / result["runId"]
            _decode_files(candidate_record["files"], candidate_copy, "candidate")
            replay = _replay_evaluation(
                fixture_paths[result["fixtureId"]], candidate_copy,
                evaluator_image=evaluation_inputs["evaluatorImage"],
            )
            expected_status = "PASS" if replay["status"] == "PASS" else "CANDIDATE_FAILURE"
            if replay["candidateHash"] != result["candidateHash"] or result["status"] != expected_status:
                raise EvidenceError(f"Evaluation replay mismatch for {result['runId']}")
    analysis = artifacts["analysis"]
    routing_campaign.validate_analysis(analysis, protocol, plan)
    computed = routing_campaign.analyze(protocol, plan, results)
    if canonical_json(computed) != canonical_json(analysis):
        raise EvidenceError("Analysis replay mismatch")

    index = artifacts["manifestIndex"]
    if not isinstance(index, list) or len(index) != len(catalog.get("tasks", [])):
        raise EvidenceError("Manifest index does not cover the catalog")
    tasks = {item["id"]: item for item in catalog["tasks"]}
    manifest_paths: list[str] = []
    for position, entry in enumerate(index):
        entry = _exact(
            entry, {"fixtureId", "template", "catalogManifestHash", "path"},
            f"$manifestIndex[{position}]",
        )
        task = tasks.get(entry["fixtureId"])
        if task is None or entry["template"] != task["template"] \
                or entry["catalogManifestHash"] != task["manifestHash"]:
            raise EvidenceError("Manifest index does not match catalog")
        if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in entry["fixtureId"]):
            raise EvidenceError("Manifest fixture IDs must be safe filename tokens")
        relative = _safe_relative(entry["path"], f"$manifestIndex[{position}].path")
        if relative != f"manifests/{entry['fixtureId']}.json":
            raise EvidenceError("Manifest path does not match fixture identity")
        manifest = _read_canonical(root / relative)
        _exact(manifest, {"schemaVersion", "contentHash"}, f"manifest {entry['fixtureId']}")
        if manifest["schemaVersion"] != 1 or manifest["contentHash"] != task["manifestHash"]:
            raise EvidenceError("Packaged fixture manifest does not match catalog")
        manifest_paths.append(relative)

    audit = _exact(
        artifacts["audit"],
        {"schemaVersion", "recordKind", "hashAlgorithm", "artifacts", "sanitization", "replay"},
        "$audit",
    )
    if audit["schemaVersion"] != 1 or audit["recordKind"] != "routing-evidence-audit" \
            or audit["hashAlgorithm"] != "sha256":
        raise EvidenceError("Unsupported evidence audit")
    expected_paths = sorted(
        list(set(ARTIFACT_PATHS.values()) - {"audit.json"}) + manifest_paths
    )
    entries = audit["artifacts"]
    if not isinstance(entries, list) or [entry.get("path") for entry in entries] != sorted(expected_paths):
        raise EvidenceError("Audit artifact inventory is incomplete or unordered")
    for position, entry in enumerate(entries):
        entry = _exact(entry, {"path", "sha256", "bytes", "mediaType"}, f"$audit.artifacts[{position}]")
        relative = _safe_relative(entry["path"], f"$audit.artifacts[{position}].path")
        path = root / relative
        if not isinstance(entry["bytes"], int) or isinstance(entry["bytes"], bool) \
                or entry["bytes"] < 0 or entry["mediaType"] != "application/json" \
                or entry["bytes"] != path.stat().st_size \
                or entry["sha256"] != sha256_file(path):
            raise EvidenceError(f"Audit mismatch for {relative}")
        _hash(entry["sha256"], f"$audit.artifacts[{position}].sha256")
    expected_inventory = set(expected_paths) | {"audit.json", "bundle.json"}
    observed_inventory = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if observed_inventory != expected_inventory:
        raise EvidenceError("Bundle contains missing or untracked files")
    sanitization = _exact(
        audit["sanitization"], {"allowedResultFields", "discardedFields", "inputDocumentHashes"},
        "$audit.sanitization",
    )
    if sanitization["allowedResultFields"] != sorted(publication_result_keys()) \
            or not isinstance(sanitization["discardedFields"], list) \
            or not isinstance(sanitization["inputDocumentHashes"], list):
        raise EvidenceError("Invalid result sanitization audit")
    if sanitization["discardedFields"] != sorted(set(sanitization["discardedFields"])) \
            or any(not isinstance(item, str) or not item for item in sanitization["discardedFields"]):
        raise EvidenceError("Discarded result field names must be unique sorted strings")
    if not sanitization["inputDocumentHashes"] \
            or sanitization["inputDocumentHashes"] != sorted(sanitization["inputDocumentHashes"]):
        raise EvidenceError("Input document hashes must be a non-empty sorted array")
    for item in sanitization["inputDocumentHashes"]:
        _hash(item, "$audit.sanitization.inputDocumentHashes[]")
    replay = _exact(audit["replay"], {
        "estimand", "analysisAlgorithm", "analysisImplementationSha256",
        "evaluationAlgorithm", "evaluationImplementationSha256",
    }, "$audit.replay")
    if replay["estimand"] != "worker" \
            or replay["analysisAlgorithm"] != "routing_campaign.analyze" \
            or replay["evaluationAlgorithm"] != "routing_evidence._replay_evaluation":
        raise EvidenceError("Unsupported replay algorithm")
    _hash(replay["analysisImplementationSha256"], "$audit.replay.analysisImplementationSha256")
    _hash(replay["evaluationImplementationSha256"], "$audit.replay.evaluationImplementationSha256")
    if replay["analysisImplementationSha256"] != artifacts["provenance"]["analysisImplementation"]["sha256"]:
        raise EvidenceError("Replay implementation does not match provenance")
    if replay["evaluationImplementationSha256"] != sha256_file(Path(__file__)):
        raise EvidenceError("Evaluation replay implementation does not match this verifier")

    expected_hashes = {
        "protocolHash": routing_campaign.value_hash(protocol),
        "runtimeManifestHash": routing_campaign.value_hash(runtime_manifest),
        "planHash": routing_campaign.value_hash(plan),
        "catalogHash": routing_campaign.value_hash(catalog),
        "resultsHash": routing_campaign.value_hash(results_document),
        "analysisHash": routing_campaign.value_hash(analysis),
        "provenanceHash": routing_campaign.value_hash(artifacts["provenance"]),
        "auditHash": routing_campaign.value_hash(audit),
    }
    if any(bundle[field] != value for field, value in expected_hashes.items()):
        raise EvidenceError("Bundle index hashes do not match packaged artifacts")
    decisions = [{
        "familyId": item["familyId"], "candidateId": item["candidateId"],
        "decision": item["decision"],
    } for item in analysis["families"]]
    counts = {
        "families": len(protocol["families"]),
        "fixtures": len(index),
        "results": len(results),
    }
    if bundle["protocolId"] != protocol["protocolId"] \
            or bundle["decisions"] != decisions or bundle["counts"] != counts:
        raise EvidenceError("Bundle summary does not match replayed evidence")
    return bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("results", nargs="+", type=Path)
    publish.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    publish.add_argument("--runtime-manifest", type=Path, default=DEFAULT_RUNTIME_MANIFEST)
    publish.add_argument(
        "--preflight", type=Path, action="append", required=True,
        help="Repeat once for every protocol machine.",
    )
    publish.add_argument("--plan", type=Path, required=True)
    publish.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    publish.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    publish.add_argument("--analysis", type=Path, required=True)
    publish.add_argument("--provenance", type=Path, required=True)
    publish.add_argument("--fixture-root", type=Path, default=ROOT / "fixtures")
    publish.add_argument(
        "--candidate-root", type=Path, required=True,
        help="Directory containing one sanitized candidate workspace per run ID",
    )
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
            "bundleFileSha256": sha256_file(args.bundle / "bundle.json"),
            "decisions": {item["familyId"]: item["decision"] for item in bundle["decisions"]},
        }, indent=2, sort_keys=True))
        return 0
    raw_results, input_hashes = _load_result_documents(args.results)
    bundle = publish_bundle(
        args.output,
        protocol=json.loads(args.protocol.read_text(encoding="utf-8")),
        runtime_manifest=json.loads(args.runtime_manifest.read_text(encoding="utf-8")),
        preflight_reports=[json.loads(path.read_text(encoding="utf-8")) for path in args.preflight],
        plan=json.loads(args.plan.read_text(encoding="utf-8")),
        matrix=json.loads(args.matrix.read_text(encoding="utf-8")),
        catalog=json.loads(args.catalog.read_text(encoding="utf-8")),
        raw_results=raw_results,
        raw_document_hashes=input_hashes,
        analysis=json.loads(args.analysis.read_text(encoding="utf-8")),
        provenance=json.loads(args.provenance.read_text(encoding="utf-8")),
        fixture_root=args.fixture_root,
        candidate_root=args.candidate_root,
        bundle_id=args.bundle_id,
    )
    print(json.dumps({
        "bundleId": bundle["bundleId"],
        "bundleIndexCanonicalSha256": routing_campaign.value_hash(bundle),
        "bundleFileSha256": sha256_file(args.output / "bundle.json"),
        "results": bundle["counts"]["results"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
