# Risk work item: Signed job payload decoder

- Risk stratum tag: `security` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: worker/codec.py calls pickle.loads on a queue-controlled payload.
- Plausible blast radius: A forged message can execute code with worker privileges.
- Compatibility/rollback constraint: Continue accepting signed JSON payload version 1.
- Approved changed paths: `src/service.py`, `src/migration.py`, `tests/test_risk_acceptance.py`. `src/contract.py` is a frozen public boundary.
- Safe rollback action: Route version 2 messages to quarantine and redeploy the v1-only decoder.
- Authorized offline acceptance command: `pytest -q tests/test_payload_codec.py`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Optimize a metrics label.
