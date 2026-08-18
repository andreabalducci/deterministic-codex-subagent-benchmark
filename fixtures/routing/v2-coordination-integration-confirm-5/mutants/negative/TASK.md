# coordination-05: gRPC reservation contract update

## Role and evidence boundary

Produce only `answer.json`. This fixture scores the quality of a worker-authored coordination and integration artifact. It does **not** ask you to spawn workers and must not claim that a live coordinator delegated, monitored, or merged anything.

## Repository/work-item snapshot

Read `repo.snapshot.json` and `WORK_ITEM.md`. The snapshot represents multiple synthetic repository files. Treat every listed path and constraint as frozen evidence. The topology is `Reservation.Proto -> {Inventory.Service, Storefront.Client}`.

## Required artifact

Return a JSON object with exactly `summary`, `workPlan`, and `integration`:

- `summary`: one sentence naming the integration and its evidence limitation.
- `workPlan.workers`: three ordered worker records. Each needs an id, exclusive owned paths, and concrete actions.
- `workPlan.frozenDependencies`: the precise contract-freeze gate.
- `workPlan.conflictChecks`: identify the seeded conflict and explicitly reject the distractor.
- `integration.mergeOrder`: dependency-safe owner ids, not file names.
- `integration.acceptanceCommands`: only the offline command authorized by the work item.
- `integration.handoffs`: producer/consumer evidence transferred at each boundary.
- `integration.evidenceBoundary`: explicitly limit claims to the authored artifact.

Use the owner ids and wording supported by the two snapshot files. Do not widen scope, assign one path to two owners, reorder a consumer before its dependency, add commands, or treat the distractor as work.
