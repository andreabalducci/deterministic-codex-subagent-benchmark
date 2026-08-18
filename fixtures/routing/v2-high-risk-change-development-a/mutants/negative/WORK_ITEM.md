# Risk work item: Payment retry idempotency

- Risk stratum tag: `concurrency` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: ledger/service.py charges before the idempotency row is committed.
- Plausible blast radius: Duplicate charges when two retry workers handle one key.
- Compatibility/rollback constraint: Keep the public charge() signature and existing SQLite test path.
- Approved changed paths: `src/service.py`, `src/migration.py`, `tests/test_risk_acceptance.py`. `src/contract.py` is a frozen public boundary.
- Safe rollback action: Feature-flag the transactional claim and retain the old table for one release.
- Authorized offline acceptance command: `pytest -q tests/test_payment_idempotency.py`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: docs/benchmark.md spelling.
