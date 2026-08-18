# Work item: Container provenance pipeline

- Topology: `build metadata -> image workflow -> attestation workflow`
- Exclusive owners in dependency order: `metadata`, `image-build`, `attestation`
- Ownership: `metadata` owns `.github/metadata.json`; `image-build` owns `.github/workflows/image.yml`; `attestation` owns `config/attest.yml` and `scripts/check-provenance.sh`.
- Freeze gate: Freeze metadata.json after worker metadata publishes its contract commit.
- Seeded conflict to detect: Image and attestation jobs derive different subject digests.
- Authorized acceptance command: `./scripts/check-provenance.sh fixtures/provenance`
- Non-goal/distractor: Renovate grouping preference.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
