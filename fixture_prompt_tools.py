#!/usr/bin/env python3
"""Regenerate diverse, scenario-bound prompts for rubric routing fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "routing"

COORD_OPENERS = (
    "Prepare a contract-first integration charter for the three owners in this dependency graph.",
    "Act as the merge-train planner: identify parallel lanes, the freeze point, and the safe join order.",
    "Audit ownership before implementation. Separate exclusive paths, handoffs, conflicts, and final proof.",
    "Build a dependency-handoff packet that another integrator could execute without guessing scope.",
    "Design a conflict-control plan for independent workstreams that converge on one acceptance run.",
    "Produce a preflight integration record: boundaries first, then owner order, transferred evidence, and checks.",
)

RISK_OPENERS = (
    "Write a change-safety dossier for the seeded failure. Start from blast radius and frozen compatibility.",
    "Treat this as a rollback-first review: define the stop condition before sequencing the smallest repair.",
    "Prepare a risk-board decision record covering defect evidence, constrained scope, rollback, and proof.",
    "Construct a compatibility-preserving migration brief; unrelated cleanup must remain outside the change.",
    "Plan the regression-first repair as if a release approver will reject any unsupported production claim.",
    "Create an incident-prevention change packet that links each implementation step to rollback evidence.",
)


def coordination_prompt(snapshot: dict, index: int) -> str:
    scenario, topology = snapshot["scenario"], snapshot["topology"]
    distractor = snapshot["distractors"][0]
    return f"""# {scenario}: integration assignment

{COORD_OPENERS[index % len(COORD_OPENERS)]}

The repository evidence is split between `repo.snapshot.json` and `WORK_ITEM.md`.
For this {snapshot['ecosystem']} scenario, the dependency shape is `{topology}`.
The item `{distractor}` is a deliberate non-goal. Do not spawn agents: this task
measures the quality of the written decomposition, not live delegation.

Write only `answer.json`, with exactly these top-level keys:

1. `summary` — identify the integration and state that conclusions are limited
   to the supplied snapshot.
2. `workPlan` — give three ordered `workers` with exclusive `owns` paths and
   concrete `actions`; also record `frozenDependencies` and `conflictChecks`.
3. `integration` — provide owner-id `mergeOrder`, the single authorized
   `acceptanceCommands` entry, boundary-specific `handoffs`, and an
   `evidenceBoundary` that does not claim execution occurred.

Respect the owner ids, dependency direction, frozen contract, seeded conflict,
and authorized command in `WORK_ITEM.md`. Reject the non-goal explicitly. A path
cannot have two owners, a consumer cannot precede its producer, and invented
commands or runtime results invalidate the artifact.
"""


def risk_prompt(snapshot: dict, index: int) -> str:
    scenario = snapshot["scenario"]
    signal = snapshot["seededSignals"][0]
    distractor = snapshot["distractors"][0]
    return f"""# {scenario}: controlled-change assignment

{RISK_OPENERS[index % len(RISK_OPENERS)]}

Use only `repo.snapshot.json` and `WORK_ITEM.md`. The deterministic signal to
explain is `{signal}`. `{distractor}` is intentionally unrelated. This is a
planning/evidence task for a compact {snapshot['ecosystem']} snapshot; do not
claim that code was changed, deployed, or observed in production.

Return only `answer.json`. Its exact sections are:

- `summary`: a bounded description of the proposed repair;
- `riskAssessment`: risk stratum, seeded defect, blast radius, compatibility
  constraints, and disposition of the distractor;
- `changePlan`: approved path scope and regression-first ordered steps;
- `rollback`: deterministic trigger, safe actions, and the compatibility state
  that must survive reversal;
- `acceptance`: only the authorized offline command, required evidence
  artifacts, and a final-review condition tied to defect, rollback, and
  compatibility proof.

Keep frozen boundaries unchanged. Do not widen the approved paths, replace
rollback with a forward-only migration, repair the distractor, or manufacture
operational evidence.
"""


def refresh() -> int:
    written = 0
    families = (
        ("v2-coordination-integration-confirm-", coordination_prompt),
        ("v2-high-risk-change-confirm-", risk_prompt),
    )
    for prefix, render in families:
        directories = sorted(
            FIXTURES.glob(prefix + "*"),
            key=lambda path: int(path.name.rsplit("-", 1)[1]),
        )
        for index, directory in enumerate(directories):
            snapshot = json.loads(
                (directory / "starter" / "repo.snapshot.json").read_text(encoding="utf-8")
            )
            prompt = render(snapshot, index)
            for variant in ("starter", "reference", "mutants/negative"):
                (directory / variant / "TASK.md").write_text(prompt, encoding="utf-8")
                written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", required=True)
    parser.parse_args()
    print(json.dumps({"written": refresh()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
