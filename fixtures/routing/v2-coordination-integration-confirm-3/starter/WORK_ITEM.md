# Work item: Ingestion cursor protocol

- Topology: `ingest/protocol.py -> ingest/http.py -> ingest/worker.py`
- Exclusive owners in dependency order: `protocol`, `http-source`, `worker`
- Ownership: `protocol` owns `src/protocol.py`; `http-source` owns `src/http_source.py`; `worker` owns `src/worker.py` and `tests/test_cursor_contract.py`.
- Freeze gate: Freeze protocol.py after worker protocol publishes its contract commit.
- Seeded conflict to detect: HTTP source emits inclusive cursors while worker assumes exclusive.
- Authorized acceptance command: `pytest -q tests/test_cursor_contract.py`
- Non-goal/distractor: Unused CSV delimiter setting.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
