# Work item: CLI profile resolution integration

- Topology: `config/schema.py -> config/loader.py -> cli/main.py`
- Exclusive owners in dependency order: `schema`, `loader`, `cli`
- Ownership: `schema` owns `src/schema.py`; `loader` owns `src/loader.py`; `cli` owns `src/main.py` and `tests/test_profile_resolution.py`.
- Freeze gate: Freeze schema.py after worker schema publishes its contract commit.
- Seeded conflict to detect: Loader and CLI disagree on missing-profile behavior.
- Authorized acceptance command: `pytest -q tests/test_profile_resolution.py`
- Non-goal/distractor: Optional color output request.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
