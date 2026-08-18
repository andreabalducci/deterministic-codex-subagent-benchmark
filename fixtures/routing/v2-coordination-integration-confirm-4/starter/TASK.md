# Release-channel workflow alignment: integration assignment

Build a dependency-handoff packet that another integrator could execute without guessing scope.

The repository evidence is split between `repo.snapshot.json` and `WORK_ITEM.md`.
For this repository-artifacts scenario, the dependency shape is `.github/actions/version -> build workflow -> release workflow`.
The item `Stale issue-label configuration` is a deliberate non-goal. Do not spawn agents: this task
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
