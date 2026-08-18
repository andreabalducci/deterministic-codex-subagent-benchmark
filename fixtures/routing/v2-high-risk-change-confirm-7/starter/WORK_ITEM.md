# Risk work item: At-least-once invoice dispatch

- Risk stratum tag: `concurrency` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: tasks/invoice.py records sent_at after publishing and retries publish duplicates.
- Plausible blast radius: Customers can receive duplicate invoices after worker termination.
- Compatibility/rollback constraint: Keep Celery acknowledgements-late and the current message schema.
- Approved changed paths: `src/service.py`, `src/migration.py`, `tests/test_risk_acceptance.py`. `src/contract.py` is a frozen public boundary.
- Safe rollback action: Disable the claim path and replay only rows without provider receipt IDs.
- Authorized offline acceptance command: `pytest -q tests/test_invoice_dispatch.py`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Change a CLI progress bar.
