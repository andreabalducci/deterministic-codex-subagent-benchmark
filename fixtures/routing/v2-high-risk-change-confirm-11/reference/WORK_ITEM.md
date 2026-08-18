# Risk work item: Payroll timezone normalization

- Risk stratum tag: `migration-compatibility` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: payroll.py treats naive timestamps as UTC although stored rows are local office time.
- Plausible blast radius: Pay-period boundaries can assign shifts to the wrong cycle.
- Compatibility/rollback constraint: Preserve exported CSV timestamps and support historical office zones.
- Approved changed paths: `src/service.py`, `src/migration.py`, `tests/test_risk_acceptance.py`. `src/contract.py` is a frozen public boundary.
- Safe rollback action: Stop the backfill, restore original timestamp columns, and switch reads back.
- Authorized offline acceptance command: `pytest -q tests/test_pay_period_boundaries.py`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Simplify a fixture factory.
