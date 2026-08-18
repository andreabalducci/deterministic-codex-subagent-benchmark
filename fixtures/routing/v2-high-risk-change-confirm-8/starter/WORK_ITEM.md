# Risk work item: Online index migration

- Risk stratum tag: `migration` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: migration.sql creates a blocking index inside the deploy transaction.
- Plausible blast radius: Writes can stall long enough to exhaust the API connection pool.
- Compatibility/rollback constraint: Support PostgreSQL 14 and preserve rollback visibility.
- Approved changed paths: `.github/workflows/change.yml`, `config/rollback.yml`, `scripts/check-risk-change.sh`. `.github/policy.yml` is a frozen public boundary.
- Safe rollback action: Cancel the index build, drop only an INVALID index, then resume traffic.
- Authorized offline acceptance command: `./scripts/check-online-migration.sh migrations/203_add_lookup.sql`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Reorder YAML keys.
