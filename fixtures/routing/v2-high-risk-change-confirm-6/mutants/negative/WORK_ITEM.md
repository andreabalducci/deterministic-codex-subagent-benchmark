# Risk work item: SSR feature-flag hydration

- Risk stratum tag: `compatibility` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: server renders checkoutV2=false while client defaults missing flags to true.
- Plausible blast radius: Hydration replaces the checkout form and loses user-entered data.
- Compatibility/rollback constraint: Preserve the server payload shape and the old checkout route.
- Approved changed paths: `src/client.ts`, `src/migration.ts`, `tests/risk.acceptance.test.tsx`. `src/contract.ts` is a frozen public boundary.
- Safe rollback action: Force checkoutV2 false at the edge and redeploy the prior client bundle.
- Authorized offline acceptance command: `pnpm test --filter checkout-hydration`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Restyle the account avatar.
