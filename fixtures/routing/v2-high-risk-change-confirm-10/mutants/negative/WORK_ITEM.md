# Risk work item: Service-worker cache schema bump

- Risk stratum tag: `migration` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: activate deletes v3 caches before all open tabs can read v4 responses.
- Plausible blast radius: Offline tabs lose queued drafts during rolling activation.
- Compatibility/rollback constraint: Retain v3 draft entries and keep the message protocol backward compatible.
- Approved changed paths: `src/client.ts`, `src/migration.ts`, `tests/risk.acceptance.test.tsx`. `src/contract.ts` is a frozen public boundary.
- Safe rollback action: Unregister v4, serve the v3 worker, and retain both cache namespaces.
- Authorized offline acceptance command: `pnpm vitest run service-worker-migration`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Update favicon metadata.
