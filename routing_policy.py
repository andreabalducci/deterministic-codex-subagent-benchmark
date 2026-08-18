#!/usr/bin/env python3
"""Validate, resolve, and render the bundled orchestration routing policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import harness
import coordinator_evidence
import construct_readiness
import routing_evidence
import routing_sequential_evidence


ROOT = Path(__file__).resolve().parent
DEFAULT_ARTIFACT = ROOT / "skills" / "orchestrate" / "routing-policy.json"
DEFAULT_SCHEMA = ROOT / "schemas" / "routing-policy.schema.json"
DEFAULT_MATRIX = ROOT / "matrix.json"
DEFAULT_SKILL = ROOT / "skills" / "orchestrate" / "SKILL.md"
EXPECTED_CONFIGURATION_COST_ORDER = [
    "luna-low", "luna-medium", "luna-high", "terra-medium", "sol-medium", "sol-high",
]
BEGIN_MARKER = "<!-- BEGIN GENERATED ROUTING"
END_MARKER = "<!-- END GENERATED ROUTING -->"
FAST_MODE_TEXT = (
    "Fast mode is a user/session-level throughput and credit-usage setting. It is not a "
    "`spawn_agent` parameter, is not required for Luna, and must never be inferred from a "
    "model or reasoning-effort selection. Say Fast mode is enabled only when the user or "
    "session state explicitly establishes that fact."
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    schema = load_json(path)
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" \
            or schema.get("additionalProperties") is not False:
        raise ValueError("Routing schema must be strict JSON Schema draft 2020-12")
    return schema


def load_matrix(path: Path = DEFAULT_MATRIX) -> list[dict[str, str]]:
    document = load_json(path)
    if not isinstance(document, dict) or set(document) != {"configurations"}:
        raise ValueError("Matrix must contain only configurations")
    configurations = document["configurations"]
    required = {"id", "model", "reasoningEffort"}
    if not isinstance(configurations, list) or not configurations:
        raise ValueError("Matrix configurations must be a non-empty array")
    if any(not isinstance(item, dict) or set(item) != required for item in configurations):
        raise ValueError("Every matrix configuration must have the exact routing fields")
    return configurations


def _validate_configuration_selection(
    routes: list[dict[str, Any]], matrix: list[dict[str, str]], cost_order: list[str]
) -> None:
    """Bind each task route to a matrix configuration and ordered fallbacks.

    Route IDs classify work; configuration IDs select a treatment.  They are
    deliberately not one-to-one: multiple task classes may use the same
    cheapest sufficient configuration.
    """
    expected = {item["id"]: (item["model"], item["reasoningEffort"]) for item in matrix}
    if cost_order != EXPECTED_CONFIGURATION_COST_ORDER:
        raise ValueError("Configuration cost order must use the frozen cheapest-to-costliest order")
    if len(cost_order) != len(set(cost_order)) or set(cost_order) != set(expected):
        raise ValueError("Configuration cost order must cover every matrix configuration exactly once")
    cost_rank = {configuration_id: index for index, configuration_id in enumerate(cost_order)}
    for route in routes:
        configuration_id = route["selectedConfigurationId"]
        selection = expected.get(configuration_id)
        if selection is None:
            raise ValueError(f"Route {route['id']} selects an unknown configuration")
        if (route["model"], route["reasoningEffort"]) != selection:
            raise ValueError(f"Route {route['id']} model and effort must match its selected configuration")
        fallback_ids = route["availabilityFallbackConfigurationIds"]
        if any(fallback_id not in expected for fallback_id in fallback_ids):
            raise ValueError(f"Route {route['id']} has an unknown availability fallback configuration")
        if any(cost_rank[fallback_id] <= cost_rank[configuration_id] for fallback_id in fallback_ids):
            raise ValueError(
                f"Route {route['id']} availability fallbacks must be costlier than its selected configuration"
            )
        if fallback_ids != sorted(fallback_ids, key=cost_rank.__getitem__):
            raise ValueError(f"Route {route['id']} availability fallbacks must be in increasing cost order")


def _validate_evidence_claims(
    policy: dict[str, Any], evidence_paths: Mapping[str, Path]
) -> None:
    metadata_by_id: dict[str, dict[str, Any]] = {}
    for metadata in policy["evidenceBundles"]:
        bundle_id = metadata["id"]
        if bundle_id in metadata_by_id:
            raise ValueError(f"Duplicate evidence bundle ID: {bundle_id}")
        metadata_by_id[bundle_id] = metadata

    resolved: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for route in policy["defaults"] + policy["coordinatorDefaults"]:
        references = route["evidenceRefs"]
        if route["claimStrength"] == "evidence-backed" and not references:
            raise ValueError(f"Evidence-backed route {route['id']} has no evidence references")
        for reference in references:
            bundle_id = reference["bundleId"]
            metadata = metadata_by_id.get(bundle_id)
            if metadata is None:
                raise ValueError(f"Route {route['id']} references unknown evidence {bundle_id}")
            if reference["taskFamily"] not in metadata["taskFamilies"]:
                raise ValueError(
                    f"Evidence {bundle_id} does not declare task family "
                    f"{reference['taskFamily']}"
                )
            if reference["taskFamily"] != route["taskClass"]:
                raise ValueError(
                    f"Evidence task family does not match route {route['id']} task class"
                )
            if reference["estimand"] != metadata["estimand"] \
                    or reference["estimand"] != route["estimand"]:
                raise ValueError(
                    f"Evidence estimand does not match route {route['id']}"
                )
            if route["claimStrength"] != "evidence-backed":
                continue
            if bundle_id not in evidence_paths:
                raise ValueError(f"Evidence-backed route {route['id']} cannot resolve {bundle_id}")
            if bundle_id not in resolved:
                bundle_path = evidence_paths[bundle_id]
                try:
                    if metadata["estimand"] == "worker":
                        index_path = bundle_path / "bundle.json"
                        bundle_index = load_json(index_path) if index_path.is_file() else {}
                        if bundle_index.get("recordKind") \
                                == routing_sequential_evidence.BUNDLE_KIND:
                            bundle = routing_sequential_evidence.verify_bundle(bundle_path)
                        else:
                            bundle = routing_evidence.verify_bundle(bundle_path)
                    elif metadata["estimand"] == coordinator_evidence.ESTIMAND:
                        bundle = coordinator_evidence.verify_bundle(bundle_path)
                    else:
                        raise ValueError(
                            f"Unsupported evidence estimand {metadata['estimand']}"
                        )
                except Exception as error:
                    raise ValueError(
                        f"Evidence bundle verification failed for {bundle_id}"
                    ) from error
                if bundle.get("bundleId") != bundle_id:
                    raise ValueError(f"Evidence bundle identity mismatch for {bundle_id}")
                if bundle.get("schemaVersion") != metadata["schemaVersion"]:
                    raise ValueError(f"Evidence schema version mismatch for {bundle_id}")
                if bundle.get("estimand") != metadata["estimand"]:
                    raise ValueError(f"Evidence estimand mismatch for {bundle_id}")
                if canonical_sha256(bundle) != metadata["canonicalSha256"]:
                    raise ValueError(f"Evidence canonical hash mismatch for {bundle_id}")
                if bundle.get("protocolHash") != metadata["protocolHash"] \
                        or bundle.get("planHash") != metadata["planHash"]:
                    raise ValueError(f"Evidence protocol or plan hash mismatch for {bundle_id}")
                bundle_families = (
                    {item.get("familyId") for item in bundle.get("decisions", [])
                     if isinstance(item, dict)}
                    if metadata["estimand"] == "worker"
                    else {bundle.get("decision", {}).get("taskFamily")}
                )
                if set(metadata["taskFamilies"]) != bundle_families:
                    raise ValueError(f"Evidence family coverage mismatch for {bundle_id}")
                analysis_relative = bundle.get("artifacts", {}).get("analysis")
                if not isinstance(analysis_relative, str):
                    raise ValueError(f"Evidence bundle has no analysis artifact for {bundle_id}")
                analysis = load_json(bundle_path / analysis_relative)
                sequential = bundle.get("recordKind") \
                    == routing_sequential_evidence.BUNDLE_KIND
                expected_kind = (
                    "routing-sequential-analysis" if sequential
                    else "routing-analysis" if metadata["estimand"] == "worker"
                    else "coordinator-analysis"
                )
                estimand_valid = metadata["estimand"] == "worker" \
                    or analysis.get("estimand") == metadata["estimand"]
                if analysis.get("recordKind") != expected_kind \
                        or analysis.get("complete") is not True or not estimand_valid:
                    raise ValueError(f"Evidence {bundle_id} is not a complete routing analysis")
                resolved[bundle_id] = (analysis, bundle)
            analysis, bundle = resolved[bundle_id]
            if metadata["estimand"] == "worker":
                families = {
                    item.get("familyId"): item for item in analysis.get("families", [])
                    if isinstance(item, dict)
                }
                family = families.get(reference["taskFamily"])
            else:
                family = analysis if bundle["decision"]["taskFamily"] \
                    == reference["taskFamily"] else None
            expected_configuration = route.get("configurationId", route.get("selectedConfigurationId"))
            if bundle.get("recordKind") == routing_sequential_evidence.BUNDLE_KIND:
                cheapest = None if family is None else family.get(
                    "cheapestSufficientConfigurationId"
                )
                if family is None or family.get("selectedTreatmentId") != expected_configuration \
                        or cheapest != expected_configuration \
                        or family.get("decision") != "ACCEPT":
                    raise ValueError(
                        f"Evidence {bundle_id} does not support route {route['id']} "
                        "as the cheapest sufficient configuration"
                    )
                if reference.get("analysisId") != bundle.get("protocolId") \
                        or reference.get("configurationId") != expected_configuration \
                        or reference.get("decision") != "ACCEPT":
                    raise ValueError(f"Evidence reference does not reproduce route {route['id']}")
                continue
            if family is None or family.get("candidateId") != expected_configuration \
                    or family.get("decision") != "SUPPORTED":
                raise ValueError(
                    f"Evidence {bundle_id} does not support route {route['id']}"
                )
            if metadata["estimand"] == "worker" and (
                family.get("cheapestSufficientConfigurationId") != expected_configuration
            ):
                raise ValueError(
                    f"Evidence {bundle_id} does not record {expected_configuration} as the cheapest sufficient selection"
                )
            gate = family.get("decisionGate", {})
            comparators = {
                item.get("comparatorId") for item in family.get("comparisons", [])
                if isinstance(item, dict)
            }
            expected_interval = [gate.get("lower95"), gate.get("upper95")]
            if reference["analysisId"] != analysis.get("protocolId") \
                    or reference["configurationId"] != expected_configuration \
                    or set(reference["comparisonConfigurationIds"]) != comparators \
                    or reference["metric"] != gate.get("metric") \
                    or reference["estimate"] != gate.get("gain") \
                    or reference["interval95"] != expected_interval \
                    or reference["decision"] != family.get("decision"):
                raise ValueError(f"Evidence reference does not reproduce route {route['id']}")


def validate_policy(
    policy: dict[str, Any],
    matrix: list[dict[str, str]],
    *,
    schema: dict[str, Any] | None = None,
    evidence_paths: Mapping[str, Path] | None = None,
    construct_readiness_path: Path | None = None,
) -> None:
    harness.validate_schema_instance(policy, schema or load_schema())
    routes = policy["defaults"]
    routes_by_id = {route["id"]: route for route in routes}
    if len(routes_by_id) != len(routes):
        raise ValueError("Routing route IDs must be unique")
    precedences = [route["precedence"] for route in routes]
    if len(precedences) != len(set(precedences)):
        raise ValueError("Routing precedence values must be unique")

    _validate_configuration_selection(routes, matrix, policy["configurationCostOrder"])
    if any(route["estimand"] != "worker" for route in routes):
        raise ValueError("Spawned-worker routing defaults must use the worker estimand")
    coordinator_routes = policy["coordinatorDefaults"]
    if len(coordinator_routes) != 1:
        raise ValueError("Exactly one live-coordinator session claim is required")
    coordinator_route = coordinator_routes[0]
    expected = {item["id"]: (item["model"], item["reasoningEffort"]) for item in matrix}
    coordinator_matrix = expected.get(coordinator_route["configurationId"])
    if coordinator_matrix != (coordinator_route["model"], coordinator_route["reasoningEffort"]):
        raise ValueError("Live-coordinator configuration must match the benchmark matrix")

    high_risk_route = policy["decisionRules"]["uncertainty"]["unknownHighRiskRouteId"]
    if high_risk_route not in routes_by_id:
        raise ValueError("Uncertainty rule references an unknown route")
    strengths = {route["claimStrength"] for route in routes + coordinator_routes}
    expected_status = (
        "provisional" if strengths == {"hypothesis"}
        else "evidence-backed" if strengths == {"evidence-backed"}
        else "mixed"
    )
    if policy["status"] != expected_status:
        raise ValueError(
            f"Policy status {policy['status']} does not match route claims ({expected_status})"
        )
    _validate_evidence_claims(policy, evidence_paths or {})
    promoted_worker_families = {
        route["taskClass"] for route in routes
        if route["claimStrength"] == "evidence-backed"
    }
    if promoted_worker_families:
        if construct_readiness_path is None:
            raise ValueError("Evidence-backed worker routes require construct readiness")
        try:
            construct_readiness.assert_families_ready(
                load_json(construct_readiness_path),
                load_json(construct_readiness.DEFAULT_PROTOCOL),
                load_json(construct_readiness.DEFAULT_CATALOG),
                promoted_worker_families,
            )
        except Exception as error:
            raise ValueError("Worker route construct-readiness verification failed") from error


def resolve_route(
    policy: dict[str, Any],
    candidate_route_ids: list[str],
    *,
    available_configuration_ids: set[str] | None = None,
    uncertain_high_risk: bool = False,
) -> dict[str, Any] | None:
    """Classify a task route, then select its cheapest available configuration."""
    routes_by_id = {route["id"]: route for route in policy["defaults"]}
    candidates = list(dict.fromkeys(candidate_route_ids))
    unknown = [route_id for route_id in candidates if route_id not in routes_by_id]
    if unknown:
        raise ValueError(f"Unknown candidate route: {unknown[0]}")
    if uncertain_high_risk:
        candidates.append(
            policy["decisionRules"]["uncertainty"]["unknownHighRiskRouteId"]
        )
        candidates = list(dict.fromkeys(candidates))
    if not candidates:
        raise ValueError("At least one candidate route is required")

    selected = min(
        (routes_by_id[route_id] for route_id in candidates),
        key=lambda route: (
            -route["safetyRank"], -route["specificity"], route["precedence"], route["id"]
        ),
    )
    configurations = {
        item["id"]: (item["model"], item["reasoningEffort"])
        for item in load_matrix()
    }
    available = set(configurations) if available_configuration_ids is None else available_configuration_ids
    for configuration_id in [selected["selectedConfigurationId"], *selected["availabilityFallbackConfigurationIds"]]:
        if configuration_id in available:
            model, effort = configurations[configuration_id]
            return {
                **selected,
                "resolvedConfigurationId": configuration_id,
                "resolvedModel": model,
                "resolvedReasoningEffort": effort,
            }
    return None


def generated_block(policy: dict[str, Any]) -> str:
    digest = canonical_sha256(policy)
    rows = "\n".join(
        f"| `{route['id']}` | {route['summary']} | `{route['selectedConfigurationId']}`: `{route['model']}`, "
        f"`reasoning_effort: \"{route['reasoningEffort']}\"` |"
        for route in policy["defaults"]
    )
    coordinator = policy["coordinatorDefaults"][0]
    return f"""## Routing defaults

{BEGIN_MARKER}
policyVersion={policy['policyVersion']}
routingArtifactCanonicalSha256={digest}
status={policy['status']}
-->

These are working defaults, not universal model-quality claims. Apply them only after deciding that delegation materially helps.

| Route | Use when | Default |
| --- | --- | --- |
{rows}

The separate live-coordinator session hypothesis is `{coordinator['model']}` with
`reasoning_effort: "{coordinator['reasoningEffort']}"` while the experiment freezes leaf
workers independently. This is a session-start choice: `spawn_agent` cannot change the
model of the already-running parent coordinator. Its evidence status is
`{coordinator['claimStrength']}` and it must never promote the spawned-worker `sol-medium` row.

First classify the task: if multiple task classes match, choose the highest safety rank, then the most specific match, then the lowest precedence number. If uncertainty leaves a higher-risk class plausible, select that class; unknown potentially high-risk traits route to `ambiguous-cross-cutting-high-risk`.

Then use that task class's evidence-selected cheapest sufficient configuration. A promoted row must be backed by a replayed machine-verifiable analysis that records this exact configuration as the cheapest sufficient selection. If it is unavailable, try only its declared cost-increasing availability fallback configurations in order. Never silently substitute an unlisted configuration. If no fallback is available, do not delegate; keep the work with the coordinator or ask for direction.

{FAST_MODE_TEXT}

Evidence status and route-level references are recorded in `routing-policy.json`.

{END_MARKER}"""


def render_skill(skill_text: str, policy: dict[str, Any]) -> str:
    block = generated_block(policy)
    if BEGIN_MARKER in skill_text:
        if skill_text.count(BEGIN_MARKER) != 1 or skill_text.count(END_MARKER) != 1:
            raise ValueError("SKILL.md must contain exactly one generated routing block")
        pattern = re.compile(
            r"## Routing defaults\n\n<!-- BEGIN GENERATED ROUTING.*?"
            + re.escape(END_MARKER),
            re.DOTALL,
        )
    else:
        if skill_text.count("## Current routing hypotheses") != 1:
            raise ValueError("Expected exactly one legacy routing block in SKILL.md")
        pattern = re.compile(
            r"## Current routing hypotheses\n\n.*?"
            r"Fast mode is .*?model or reasoning effort was set\.",
            re.DOTALL,
        )
    rendered, count = pattern.subn(block, skill_text, count=1)
    if count != 1:
        raise ValueError("Expected exactly one routing block in SKILL.md")
    return rendered


def parse_evidence_paths(values: list[str]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for value in values:
        bundle_id, separator, path = value.partition("=")
        if not separator or not bundle_id or not path or bundle_id in paths:
            raise ValueError("Evidence paths must be unique ID=PATH values")
        paths[bundle_id] = Path(path)
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    parser.add_argument("--evidence", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--construct-readiness", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = load_json(args.artifact)
    validate_policy(
        policy,
        load_matrix(args.matrix),
        schema=load_schema(args.schema),
        evidence_paths=parse_evidence_paths(args.evidence),
        construct_readiness_path=args.construct_readiness,
    )
    current = args.skill.read_text(encoding="utf-8")
    rendered = render_skill(current, policy)
    if args.check:
        if rendered != current:
            print("SKILL.md routing block is out of date", file=sys.stderr)
            return 1
        print(f"Routing policy is synchronized ({canonical_sha256(policy)})")
        return 0
    if args.write:
        args.skill.write_text(rendered, encoding="utf-8")
        print(args.skill)
        return 0
    print(generated_block(policy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
