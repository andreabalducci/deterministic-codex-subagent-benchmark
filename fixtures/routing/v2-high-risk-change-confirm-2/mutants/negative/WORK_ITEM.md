# Risk work item: Single-flight token refresh

- Risk stratum tag: `concurrency-security` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: authClient.ts starts one refresh per failed request and overwrites rotated tokens.
- Plausible blast radius: Parallel 401 responses can invalidate the valid refresh token.
- Compatibility/rollback constraint: Do not change the fetch wrapper API or persist tokens to localStorage.
- Approved changed paths: `src/client.ts`, `src/migration.ts`, `tests/risk.acceptance.test.tsx`. `src/contract.ts` is a frozen public boundary.
- Safe rollback action: Disable the single-flight flag and restore the previous in-memory token module.
- Authorized offline acceptance command: `pnpm vitest run authClient.concurrent`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Move a login illustration.
