# Work item: Async job-result contract

- Topology: `jobs/contracts.py -> {jobs/executor.py, api/status.py}`
- Exclusive owners in dependency order: `contracts`, `executor`, `status-api`
- Ownership: `contracts` owns `src/contracts.py`; `executor` owns `src/executor.py`; `status-api` owns `src/status.py` and `tests/test_job_result_contract.py`.
- Freeze gate: Freeze contracts.py after worker contracts publishes its contract commit.
- Seeded conflict to detect: Executor returns error_detail while API reads error.
- Authorized acceptance command: `pytest -q tests/test_job_result_contract.py`
- Non-goal/distractor: Formatter upgrade proposal.
- Handoff rule: `contracts` passes the frozen contract to both branches; `executor` and `status-api` independently return focused results and conflict statements to the integrator.
