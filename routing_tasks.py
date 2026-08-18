#!/usr/bin/env python3
"""V2 manifest-driven routing-task catalog and deterministic artifact evaluator.

This deliberately does not invoke agents or claim to exercise live delegation.  In
particular, the coordination fixtures assess a worker's planning/integration artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import harness

ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "fixtures" / "catalog.json"
SCHEMA_PATH = ROOT / "schemas" / "routing-task.schema.json"
IGNORED = {".git", "__pycache__", "node_modules", "bin", "obj"}
FAMILIES = {
    "mechanical", "bounded-mapping-patch", "isolated-implementation",
    "read-heavy-analysis", "coordination-integration", "high-risk-change",
}
ADAPTERS = {
    "json-semantic-diff-v1", "artifact-rubric-v1", "command-test-v1",
}
ROUTING_EVALUATOR_IMAGE = "codex-routing-evaluator:10.0.301-node22-python3.12"
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
ARTIFACT_PASS_SCORE = 0.80
WORD_RE = re.compile(r"[a-z0-9_.:/-]+")
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "is", "it", "of", "on", "or", "the", "this", "to", "with",
    "after", "before", "only", "when", "without", "must", "not",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def safe_relative(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def strict_child(path: Path, parent: Path) -> Path:
    resolved, root = path.resolve(), parent.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"path escapes root: {path}")
    return resolved


def visible_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not any(part in IGNORED for part in path.relative_to(root).parts)
    )


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_descriptor(item: dict[str, Any]) -> None:
    required = {"id", "family", "kind", "template", "adapter", "ecosystem", "surface", "development", "manifestHash"}
    if set(item) != required:
        raise ValueError(f"descriptor {item.get('id')!r} has an invalid field set")
    if not isinstance(item["id"], str) or not item["id"].strip():
        raise ValueError("descriptor id must be non-empty")
    if item["family"] not in FAMILIES or item["adapter"] not in ADAPTERS:
        raise ValueError(f"unsupported family or adapter for {item['id']}")
    if item["kind"] not in {"development", "confirmatory"} or not isinstance(item["development"], bool):
        raise ValueError(f"invalid development shape for {item['id']}")
    if item["development"] != (item["kind"] == "development"):
        raise ValueError(f"development flag disagrees with kind for {item['id']}")
    safe_relative(item["template"])
    if not isinstance(item["manifestHash"], str) or len(item["manifestHash"]) != 64:
        raise ValueError(f"invalid manifest hash for {item['id']}")
    if not isinstance(item["ecosystem"], str) or not isinstance(item["surface"], str):
        raise ValueError(f"invalid ecosystem metadata for {item['id']}")


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    schema = load_schema()
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" \
            or schema.get("additionalProperties") is not False:
        raise ValueError("routing task schema must be strict draft 2020-12")
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {"schemaVersion", "tasks"} or document["schemaVersion"] != 2:
        raise ValueError("catalog must be strict schemaVersion 2")
    if not isinstance(document["tasks"], list):
        raise ValueError("catalog tasks must be an array")
    for task in document["tasks"]:
        validate_descriptor(task)
    ids = [task["id"] for task in document["tasks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("catalog task IDs must be unique")
    templates = [task["template"] for task in document["tasks"]]
    if len(templates) != len(set(templates)):
        raise ValueError("catalog templates must be unique; aliases are pseudoreplicates")
    for task in document["tasks"]:
        verify_template_manifest(task)
        validate_template_quality(task)
    for family in FAMILIES:
        tasks = [task for task in document["tasks"] if task["family"] == family]
        if len([task for task in tasks if task["kind"] == "development"]) != 2:
            raise ValueError(f"{family} needs exactly two development fixtures")
        if len([task for task in tasks if task["kind"] == "confirmatory"]) != 12:
            raise ValueError(f"{family} needs exactly twelve sealed confirmatory descriptors")
        if len({task["ecosystem"] for task in tasks}) < 3:
            raise ValueError(f"{family} must span at least three ecosystems")
        prompt_hashes = {
            sha256_file(template_root(task) / "starter" / "TASK.md") for task in tasks
        }
        context_hashes = {starter_context_hash(task) for task in tasks}
        if len(prompt_hashes) != len(tasks) or len(context_hashes) != len(tasks):
            raise ValueError(f"{family} contains duplicate prompts or visible contexts")
    return document


def task_by_id(task_id: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or load_catalog()
    matches = [task for task in catalog["tasks"] if task["id"] == task_id]
    if len(matches) != 1:
        raise ValueError(f"unknown task ID: {task_id}")
    return matches[0]


def template_root(task: dict[str, Any]) -> Path:
    return strict_child(ROOT / "fixtures" / safe_relative(task["template"]), ROOT / "fixtures")


def template_content_hash(root: Path) -> str:
    entries = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in visible_files(root) if path.name != "fixture-manifest.json"
    }
    return sha256_bytes(canonical_json(entries).encode("utf-8"))


def verify_template_manifest(task: dict[str, Any]) -> None:
    root = template_root(task)
    manifest_path = root / "fixture-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"missing or invalid fixture manifest for {task['id']}") from error
    if set(manifest) != {"schemaVersion", "contentHash"} or manifest["schemaVersion"] != 1:
        raise ValueError(f"invalid fixture manifest for {task['id']}")
    actual = template_content_hash(root)
    if manifest["contentHash"] != actual or task["manifestHash"] != actual:
        raise ValueError(f"fixture manifest hash mismatch for {task['id']}")


def refresh_manifests(path: Path = CATALOG_PATH) -> dict[str, Any]:
    """Regenerate every fixture manifest and bind the catalog to the new hashes."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {"schemaVersion", "tasks"} or document["schemaVersion"] != 2:
        raise ValueError("catalog must be strict schemaVersion 2")
    for task in document["tasks"]:
        validate_descriptor(task)
        root = template_root(task)
        digest = template_content_hash(root)
        manifest = {"schemaVersion": 1, "contentHash": digest}
        harness.atomic_write(
            root / "fixture-manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            mode=0o644,
        )
        task["manifestHash"] = digest
    harness.atomic_write(
        path,
        (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o644,
    )
    return load_catalog(path)


def validate_template_quality(task: dict[str, Any]) -> None:
    root = template_root(task)
    prompt = (root / "starter" / "TASK.md").read_text(encoding="utf-8")
    normalized = " ".join(prompt.lower().split())
    forbidden = (
        "produce the approved artifact", "apply requested low-risk change",
        "inspect visible source and apply", "placeholder", "todo: describe",
    )
    context = [p for p in visible_files(root / "starter") if p.name != "TASK.md"]
    if len(prompt) < 180 or not context or any(phrase in normalized for phrase in forbidden):
        raise ValueError(f"placeholder-quality fixture rejected for {task['id']}")


def starter_context_hash(task: dict[str, Any]) -> str:
    """Hash visible task inputs, excluding the prompt and blank worker output shells."""
    root = template_root(task) / "starter"
    entries: dict[str, str] = {}
    mutable = set(load_template(task)["mutable"])
    for path in visible_files(root):
        relative = path.relative_to(root).as_posix()
        if relative != "TASK.md" and relative not in mutable:
            entries[relative] = sha256_file(path)
    # Exact-file tasks intentionally edit their only input, so include its starter bytes.
    if not entries:
        for relative in sorted(mutable):
            entries[relative] = sha256_file(root / relative)
    return sha256_bytes(canonical_json(entries).encode("utf-8"))


def load_template(task: dict[str, Any]) -> dict[str, Any]:
    root = template_root(task)
    descriptor = json.loads((root / "task.json").read_text(encoding="utf-8"))
    expected = {"schemaVersion", "adapter", "mutable", "immutable", "required", "rubric"}
    if descriptor.get("adapter") == "command-test-v1":
        expected |= {"evaluatorProfile", "command"}
    if set(descriptor) != expected or descriptor["schemaVersion"] != 1:
        raise ValueError(f"invalid task template: {task['template']}")
    if descriptor["adapter"] != task["adapter"]:
        raise ValueError(f"adapter mismatch for {task['id']}")
    for key in ("mutable", "immutable", "required"):
        if not isinstance(descriptor[key], list) or not all(isinstance(x, str) for x in descriptor[key]):
            raise ValueError(f"invalid {key} list for {task['id']}")
        for value in descriptor[key]: safe_relative(value)
    if set(descriptor["mutable"]) & set(descriptor["immutable"]):
        raise ValueError(f"overlapping mutable and immutable files for {task['id']}")
    return descriptor


def materialize(task_id: str, destination: Path, *, replace: bool = False) -> Path:
    task, destination = task_by_id(task_id), destination.resolve()
    if destination.exists():
        if not replace: raise FileExistsError(destination)
        shutil.rmtree(destination)
    source = template_root(task) / "starter"
    shutil.copytree(source, destination)
    # Sealed answer keys, references, and mutants live beside starter and are never copied.
    if any((destination / name).exists() for name in ("sealed", "reference", "mutants")):
        raise ValueError("sealed fixture material leaked into worker workspace")
    return destination


def candidate_integrity(task: dict[str, Any], candidate: Path) -> list[str]:
    spec, root = load_template(task), candidate.resolve()
    errors: list[str] = []
    if not root.exists() or root.is_symlink(): return ["candidate root is missing or a symlink"]
    starter = template_root(task) / "starter"
    expected_files = set(spec["mutable"]) | set(spec["immutable"]) | set(spec["required"])
    actual_files = {path.relative_to(root).as_posix() for path in visible_files(root)}
    for relative in sorted(expected_files - actual_files): errors.append(f"missing required file: {relative}")
    for relative in sorted(actual_files - expected_files): errors.append(f"unexpected candidate file: {relative}")
    for relative in spec["immutable"]:
        if (root / relative).exists() and sha256_file(root / relative) != sha256_file(starter / relative):
            errors.append(f"modified immutable file: {relative}")
    for path in root.rglob("*"):
        if path.is_symlink(): errors.append(f"symbolic links are forbidden: {path.relative_to(root)}")
    return errors


def candidate_hash(task: dict[str, Any], candidate: Path) -> str:
    spec = load_template(task)
    entries = {relative: sha256_file(candidate / relative) for relative in sorted(spec["mutable"])}
    return sha256_bytes(canonical_json(entries).encode())


def _tokens(value: Any) -> set[str]:
    """Return stable content atoms without treating prose as an exact answer key."""
    if isinstance(value, dict):
        text = " ".join(str(item) for pair in value.items() for item in pair)
    elif isinstance(value, list):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value)
    return {token for token in WORD_RE.findall(text.lower()) if token not in STOP_WORDS}


def _semantic_text(actual: Any, expected: Any, *, recall: float = 0.45) -> bool:
    """Accept paraphrase while requiring deterministic coverage of evidence atoms."""
    if not isinstance(actual, str) or not actual.strip():
        return False
    if "unsupported mutant claim" in actual.lower():
        return False
    required = _tokens(expected)
    observed = _tokens(actual)
    return bool(required) and len(required & observed) / len(required) >= recall


def _semantic_text_collection(actual: Any, expected: Any) -> bool:
    if not isinstance(actual, list) or not actual or not all(
        isinstance(item, str) and item.strip() for item in actual
    ):
        return False
    return _semantic_text(" ".join(actual), " ".join(expected), recall=0.45)


def _path_atoms(value: Any) -> set[str]:
    return {token for token in _tokens(value) if "/" in token or token.endswith(
        (".cs", ".ts", ".py", ".json", ".sql", ".yml", ".yaml")
    )}


def _criterion(identifier: str, passed: bool, *, critical: bool = True) -> dict[str, Any]:
    return {
        "id": identifier, "outcome": "PASS" if passed else "FAIL",
        "score": 1.0 if passed else 0.0, "critical": critical,
    }


def _strict_top_level(actual: Any, fields: list[str]) -> bool:
    return isinstance(actual, dict) and set(actual) == set(fields)


def _read_heavy_outcomes(
    fields: list[str], actual: dict[str, Any], expected: dict[str, Any]
) -> list[dict[str, Any]]:
    assessment, findings, evidence, exclusions = fields
    actual_findings = actual.get(findings)
    expected_findings = expected[findings]
    finding_ids = lambda value: {
        item.get("factId") for item in value
        if isinstance(item, dict) and isinstance(item.get("factId"), str)
    } if isinstance(value, list) else set()
    findings_valid = (
        isinstance(actual_findings, list)
        and finding_ids(actual_findings) == finding_ids(expected_findings)
        and all(isinstance(item, dict) and _semantic_text(
            item.get("statement"), next(
                gold["statement"] for gold in expected_findings
                if gold["factId"] == item.get("factId")
            ), recall=0.35,
        ) for item in actual_findings)
    )

    def evidence_set(value: Any) -> set[tuple[Any, ...]]:
        if not isinstance(value, list):
            return set()
        return {
            (item.get("factId"), item.get("path"), item.get("line"), item.get("excerpt"))
            for item in value if isinstance(item, dict)
        }

    return [
        _criterion(assessment, _semantic_text(actual.get(assessment), expected[assessment]), critical=False),
        _criterion(findings, findings_valid),
        _criterion(evidence, evidence_set(actual.get(evidence)) == evidence_set(expected[evidence])),
        _criterion(exclusions, _semantic_text_collection(actual.get(exclusions), expected[exclusions])),
    ]


def _coordination_outcomes(
    actual: dict[str, Any], expected: dict[str, Any]
) -> list[dict[str, Any]]:
    work, gold_work = actual.get("workPlan"), expected["workPlan"]
    integration, gold_integration = actual.get("integration"), expected["integration"]
    work = work if isinstance(work, dict) else {}
    integration = integration if isinstance(integration, dict) else {}
    workers, gold_workers = work.get("workers"), gold_work["workers"]
    worker_shape = isinstance(workers, list) and len(workers) == len(gold_workers)
    if worker_shape:
        for worker, gold in zip(workers, gold_workers):
            worker_shape = worker_shape and isinstance(worker, dict)
            if not worker_shape:
                break
            worker_shape = (
                set(worker) == {"id", "owns", "actions"}
                and worker["id"] == gold["id"]
                and set(worker["owns"]) == set(gold["owns"])
                and isinstance(worker["actions"], list)
                and len(worker["actions"]) >= len(gold["actions"])
                and _semantic_text_collection(worker["actions"], gold["actions"])
            )
            if not worker_shape:
                break
    frozen = work.get("frozenDependencies")
    frozen_ok = _semantic_text_collection(frozen, gold_work["frozenDependencies"]) \
        and _path_atoms(frozen) == _path_atoms(gold_work["frozenDependencies"])
    conflicts_ok = _semantic_text_collection(
        work.get("conflictChecks"), gold_work["conflictChecks"]
    )

    def handoff_edges(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.split(":", 1)[0].strip() for item in value if isinstance(item, str)]

    return [
        _criterion("summary", _semantic_text(actual.get("summary"), expected["summary"]), critical=False),
        _criterion("workPlan.workers", worker_shape),
        _criterion("workPlan.frozenDependencies", frozen_ok),
        _criterion("workPlan.conflictChecks", conflicts_ok),
        _criterion("integration.mergeOrder", integration.get("mergeOrder") == gold_integration["mergeOrder"]),
        _criterion("integration.acceptanceCommands", integration.get("acceptanceCommands") == gold_integration["acceptanceCommands"]),
        _criterion("integration.handoffs", handoff_edges(integration.get("handoffs")) == handoff_edges(gold_integration["handoffs"])),
        _criterion("integration.evidenceBoundary", _semantic_text(
            integration.get("evidenceBoundary"), gold_integration["evidenceBoundary"], recall=0.35
        )),
    ]


def _high_risk_outcomes(
    actual: dict[str, Any], expected: dict[str, Any]
) -> list[dict[str, Any]]:
    risk = actual.get("riskAssessment") if isinstance(actual.get("riskAssessment"), dict) else {}
    gold_risk = expected["riskAssessment"]
    plan = actual.get("changePlan") if isinstance(actual.get("changePlan"), dict) else {}
    gold_plan = expected["changePlan"]
    rollback = actual.get("rollback") if isinstance(actual.get("rollback"), dict) else {}
    gold_rollback = expected["rollback"]
    acceptance = actual.get("acceptance") if isinstance(actual.get("acceptance"), dict) else {}
    gold_acceptance = expected["acceptance"]
    risk_ok = (
        risk.get("stratum") == gold_risk["stratum"]
        and _semantic_text(risk.get("seededDefect"), gold_risk["seededDefect"], recall=0.50)
        and _semantic_text(risk.get("blastRadius"), gold_risk["blastRadius"], recall=0.40)
        and _semantic_text_collection(risk.get("constraints"), gold_risk["constraints"])
        and _semantic_text(risk.get("distractorDisposition"), gold_risk["distractorDisposition"], recall=0.35)
    )
    return [
        _criterion("summary", _semantic_text(actual.get("summary"), expected["summary"]), critical=False),
        _criterion("riskAssessment", risk_ok),
        _criterion("changePlan.scope", set(plan.get("scope", [])) == set(gold_plan["scope"])),
        _criterion("changePlan.steps", _semantic_text_collection(plan.get("steps"), gold_plan["steps"])),
        _criterion("rollback.trigger", _semantic_text(rollback.get("trigger"), gold_rollback["trigger"], recall=0.40)),
        _criterion("rollback.actions", _semantic_text_collection(rollback.get("actions"), gold_rollback["actions"])),
        _criterion("rollback.compatibility", _semantic_text(rollback.get("compatibility"), gold_rollback["compatibility"], recall=0.40)),
        _criterion("acceptance.commands", acceptance.get("commands") == gold_acceptance["commands"]),
        _criterion("acceptance.artifacts", set(acceptance.get("artifacts", [])) == set(gold_acceptance["artifacts"])),
        _criterion("acceptance.finalReview", _semantic_text(
            acceptance.get("finalReview"), gold_acceptance["finalReview"], recall=0.35
        )),
    ]


def artifact_outcomes(
    task: dict[str, Any], spec: dict[str, Any], actual: Any, expected: dict[str, Any]
) -> tuple[bool, list[dict[str, Any]]]:
    fields = spec["rubric"]
    schema_valid = _strict_top_level(actual, fields)
    if not schema_valid:
        return False, [_criterion("strict-schema", False)]
    if task["family"] == "read-heavy-analysis":
        return True, _read_heavy_outcomes(fields, actual, expected)
    if task["family"] == "coordination-integration":
        return True, _coordination_outcomes(actual, expected)
    if task["family"] == "high-risk-change":
        return True, _high_risk_outcomes(actual, expected)
    raise ValueError(f"artifact rubric is unsupported for family {task['family']}")


def evaluate_artifact(
    task_id: str,
    candidate: Path,
    *,
    catalog: dict[str, Any] | None = None,
    backend: str = "docker",
    trusted_native: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
    task = task_by_id(task_id, catalog)
    spec = load_template(task)
    integrity = candidate_integrity(task, candidate)
    report: dict[str, Any] = {
        "schemaVersion": 3, "taskId": task_id, "family": task["family"],
        "adapter": task["adapter"], "candidateHash": None, "status": "FAIL",
        "schemaValid": False, "rubricScore": 0.0, "criticalPassed": False,
        "integrityViolations": integrity, "outcomes": [],
    }
    if integrity: return report
    report["candidateHash"] = candidate_hash(task, candidate)
    sealed = json.loads((template_root(task) / "sealed" / "expected.json").read_text(encoding="utf-8"))
    if task["adapter"] == "command-test-v1":
        if backend == "native" and not trusted_native:
            raise ValueError("Native command evaluation is restricted to trusted calibration artifacts")
        evaluator = json.loads(
            (template_root(task) / "sealed" / "evaluator.json").read_text(encoding="utf-8")
        )
        if not isinstance(evaluator, dict) or evaluator.get("schemaVersion") != 1 \
                or evaluator.get("adapter") != "sealed-command-v1" \
                or not isinstance(evaluator.get("command"), list):
            raise ValueError(f"Invalid sealed evaluator for {task_id}")
        raw_command = evaluator["command"]
        if not raw_command or not all(isinstance(item, str) and item for item in raw_command):
            raise ValueError(f"Invalid sealed evaluator command for {task_id}")
        if backend == "native":
            command = [
                item.replace("{candidate}", str(candidate.resolve())) for item in raw_command
            ]
            cwd = template_root(task)
            attempt = harness.execute_captured(
                command, cwd=cwd, environment=None, timeout=timeout,
                limit=MAX_COMMAND_OUTPUT_BYTES,
            )
        elif backend == "docker":
            # Never expose references or mutants to candidate code. The oracle bundle
            # contains only the minimum starter inputs and sealed evaluator assets.
            with tempfile.TemporaryDirectory(prefix="routing-oracle-") as temporary:
                oracle = Path(temporary) / "fixture"
                shutil.copytree(template_root(task) / "sealed", oracle / "sealed")
                shutil.copytree(template_root(task) / "starter", oracle / "starter")
                command = [
                    "docker", "run", "--rm", "--network", "none", "--read-only",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", "128", "--memory", "1g", "--cpus", "2",
                    *harness.docker_identity_arguments(),
                    "--tmpfs", harness.docker_tmpfs("/tmp", "rw,noexec,nosuid,size=128m"),
                    "--mount", f"type=bind,src={oracle.resolve()},dst=/fixture,readonly",
                    "--mount", f"type=bind,src={candidate.resolve()},dst=/candidate,readonly",
                    "--workdir", "/fixture", ROUTING_EVALUATOR_IMAGE,
                    *[item.replace("{candidate}", "/candidate") for item in raw_command],
                ]
                attempt = harness.execute_captured(
                    command, cwd=ROOT, environment=None, timeout=timeout,
                    limit=MAX_COMMAND_OUTPUT_BYTES,
                )
        else:
            raise ValueError(f"Unsupported command evaluation backend: {backend}")
        passed = (
            attempt["exitCode"] == 0 and not attempt["timedOut"]
            and not attempt["launcherFailure"] and not attempt["outputLimitExceeded"]
        )
        report["schemaValid"] = True
        report["outcomes"] = [{
            **_criterion("sealed-command", passed), "attempt": attempt,
        }]
    elif task["adapter"] == "json-semantic-diff-v1":
        relative = spec["mutable"][0]
        try:
            actual = json.loads((candidate / relative).read_text(encoding="utf-8"))
            expected = json.loads(
                (template_root(task) / "sealed" / relative).read_text(encoding="utf-8")
            )
            report["schemaValid"] = True
            report["outcomes"] = [_criterion("allowed-json-diff", actual == expected)]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            report["outcomes"] = [_criterion("valid-json", False)]
    else:
        relative = spec["mutable"][0]
        try: actual = json.loads((candidate / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError): actual = None
        report["schemaValid"], report["outcomes"] = artifact_outcomes(
            task, spec, actual, sealed
        )
    outcomes = report["outcomes"]
    report["rubricScore"] = round(
        sum(item["score"] for item in outcomes) / len(outcomes), 6
    ) if outcomes else 0.0
    report["criticalPassed"] = bool(outcomes) and all(
        item["outcome"] == "PASS" for item in outcomes if item["critical"]
    )
    report["status"] = "PASS" if (
        report["schemaValid"] and report["criticalPassed"]
        and report["rubricScore"] >= ARTIFACT_PASS_SCORE
    ) else "FAIL"
    return report


def calibrate(task_id: str | None = None, *, backend: str = "native") -> dict[str, Any]:
    catalog = load_catalog(); tasks = catalog["tasks"]
    if task_id: tasks = [task_by_id(task_id, catalog)]
    roots = sorted({template_root(task) for task in tasks})
    cases = []
    for root in roots:
        representative = next(task for task in tasks if template_root(task) == root)
        for label, expected in (("reference", "PASS"), ("mutant", "FAIL")):
            candidate = root / "reference" if label == "reference" else root / "mutants" / "negative"
            actual = evaluate_artifact(
                representative["id"], candidate, catalog=catalog,
                backend=backend, trusted_native=True,
            )["status"]
            cases.append({"template": representative["template"], "case": label, "expected": expected, "actual": actual})
        if representative["adapter"] in {
            "json-semantic-diff-v1", "artifact-rubric-v1", "command-test-v1"
        }:
            with tempfile.TemporaryDirectory(prefix="routing-positive-") as temporary:
                equivalent = Path(temporary) / "candidate"
                shutil.copytree(root / "reference", equivalent)
                mutable = load_template(representative)["mutable"]
                relative = (
                    mutable[-1]
                    if representative["adapter"] == "command-test-v1" else mutable[0]
                )
                artifact = equivalent / relative
                if representative["adapter"] == "json-semantic-diff-v1":
                    value = json.loads(artifact.read_text(encoding="utf-8"))
                    # Reverse insertion order and compact formatting: semantic JSON is unchanged.
                    if isinstance(value, dict):
                        value = dict(reversed(list(value.items())))
                    artifact.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
                elif representative["adapter"] == "artifact-rubric-v1":
                    value = json.loads(artifact.read_text(encoding="utf-8"))
                    first = load_template(representative)["rubric"][0]
                    value[first] = f"{value[first]} — same evidence, restated."
                    artifact.write_text(json.dumps(value, indent=4), encoding="utf-8")
                elif artifact.suffix == ".json":
                    value = json.loads(artifact.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        value = dict(reversed(list(value.items())))
                    artifact.write_text(json.dumps(value, indent=1), encoding="utf-8")
                else:
                    comment = {
                        ".py": "# semantically equivalent calibration variant",
                        ".cs": "// semantically equivalent calibration variant",
                        ".ts": "// semantically equivalent calibration variant",
                        ".sql": "-- semantically equivalent calibration variant",
                    }.get(artifact.suffix)
                    if comment is None:
                        raise ValueError(
                            f"No equivalent-positive transform for {artifact.suffix}"
                        )
                    artifact.write_text(
                        artifact.read_text(encoding="utf-8").rstrip() + "\n" + comment + "\n",
                        encoding="utf-8",
                    )
                actual = evaluate_artifact(
                    representative["id"], equivalent, catalog=catalog,
                    backend=backend, trusted_native=True,
                )["status"]
                cases.append({
                    "template": representative["template"], "case": "equivalent-positive",
                    "expected": "PASS", "actual": actual,
                })
        if representative["adapter"] == "artifact-rubric-v1":
            with tempfile.TemporaryDirectory(prefix="routing-schema-mutant-") as temporary:
                mutant = Path(temporary) / "candidate"
                shutil.copytree(root / "reference", mutant)
                relative = load_template(representative)["mutable"][0]
                artifact = mutant / relative
                value = json.loads(artifact.read_text(encoding="utf-8"))
                value["unexpectedBenchmarkField"] = True
                artifact.write_text(json.dumps(value), encoding="utf-8")
                actual = evaluate_artifact(
                    representative["id"], mutant, catalog=catalog,
                    backend=backend, trusted_native=True,
                )["status"]
                cases.append({
                    "template": representative["template"], "case": "schema-extra-mutant",
                    "expected": "FAIL", "actual": actual,
                })
    return {"schemaVersion": 3, "passed": all(case["expected"] == case["actual"] for case in cases), "cases": cases}


def docker_calibration_artifact(report: dict[str, Any]) -> dict[str, Any]:
    """Bind a complete Docker calibration to the catalog and evaluator image."""
    image = harness.docker_image_info(ROUTING_EVALUATOR_IMAGE)
    return {
        "schemaVersion": 1,
        "recordKind": "routing-calibration",
        "backend": "docker",
        "catalogHash": harness.sha256_bytes(
            harness.canonical_json(load_catalog()).encode("utf-8")
        ),
        "evaluatorImage": ROUTING_EVALUATOR_IMAGE,
        "evaluatorImageId": image["id"],
        "passed": report["passed"],
        "cases": report["cases"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog-validate")
    manifests = sub.add_parser("manifest"); manifests.add_argument("--write", action="store_true")
    mat = sub.add_parser("materialize"); mat.add_argument("--task", required=True); mat.add_argument("--destination", type=Path, required=True); mat.add_argument("--replace", action="store_true")
    ev = sub.add_parser("evaluate-artifact"); ev.add_argument("--task", required=True); ev.add_argument("--candidate", type=Path, required=True); ev.add_argument("--backend", choices=["docker"], default="docker")
    cal = sub.add_parser("calibrate"); cal.add_argument("--all", action="store_true"); cal.add_argument("--task"); cal.add_argument("--backend", choices=["native", "docker"], default="native"); cal.add_argument("--artifact-output", type=Path)
    args = parser.parse_args()
    if args.command == "catalog-validate":
        catalog = load_catalog(); print(json.dumps({"valid": True, "tasks": len(catalog["tasks"])})); return 0
    if args.command == "manifest":
        if args.write:
            catalog = refresh_manifests()
            print(json.dumps({"written": len(catalog["tasks"])}))
            return 0
        catalog = load_catalog()
        print(json.dumps({"valid": True, "tasks": len(catalog["tasks"])}))
        return 0
    if args.command == "materialize": print(materialize(args.task, args.destination, replace=args.replace)); return 0
    if args.command == "evaluate-artifact":
        report = evaluate_artifact(args.task, args.candidate, backend=args.backend); print(json.dumps(report, sort_keys=True)); return 0 if report["status"] == "PASS" else 1
    if args.command == "calibrate":
        if not args.all and not args.task: parser.error("calibrate requires --all or --task")
        if args.artifact_output and (args.backend != "docker" or not args.all or args.task):
            parser.error("--artifact-output requires --all --backend docker")
        report = calibrate(args.task, backend=args.backend)
        if args.artifact_output:
            harness.save_json(
                args.artifact_output, docker_calibration_artifact(report), replace=False
            )
        print(json.dumps(report, sort_keys=True)); return 0 if report["passed"] else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
