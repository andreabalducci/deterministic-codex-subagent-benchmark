# Work item: Release-channel workflow alignment

- Topology: `.github/actions/version -> build workflow -> release workflow`
- Exclusive owners in dependency order: `version-action`, `build-workflow`, `release-workflow`
- Ownership: `version-action` owns `.github/action.yml`; `build-workflow` owns `.github/workflows/build.yml`; `release-workflow` owns `config/release.yml` and `scripts/verify-release.sh`.
- Freeze gate: Freeze action.yml after worker version-action publishes its contract commit.
- Seeded conflict to detect: Build and release jobs write the same artifact label differently.
- Authorized acceptance command: `./scripts/verify-release.sh --offline`
- Non-goal/distractor: Stale issue-label configuration.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
