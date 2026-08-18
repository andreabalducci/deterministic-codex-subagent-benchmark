# Work item: Helm environment-key convergence

- Topology: `config schema -> {image entrypoint, Helm values}`
- Exclusive owners in dependency order: `config-schema`, `entrypoint`, `helm`
- Ownership: `config-schema` owns `.github/config.schema.json`; `entrypoint` owns `.github/workflows/entrypoint.sh`; `helm` owns `config/values.yaml` and `scripts/verify-config-contract.sh`.
- Freeze gate: Freeze config.schema.json after worker config-schema publishes its contract commit.
- Seeded conflict to detect: Entrypoint and chart use different names for the same timeout.
- Authorized acceptance command: `./scripts/verify-config-contract.sh charts/service`
- Non-goal/distractor: Development-only compose port.
- Handoff rule: `config-schema` passes the frozen contract to both branches; `entrypoint` and `helm` independently return focused results and conflict statements to the integrator.
