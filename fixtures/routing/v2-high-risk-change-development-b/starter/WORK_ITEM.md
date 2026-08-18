# Risk work item: Release signing permission reduction

- Risk stratum tag: `security` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: release.yml grants write-all and runs an unpinned third-party signer.
- Plausible blast radius: Compromised action can modify repository contents and releases.
- Compatibility/rollback constraint: Keep OIDC publication and the manual approval environment.
- Approved changed paths: `.github/workflows/change.yml`, `config/rollback.yml`, `scripts/check-risk-change.sh`. `.github/policy.yml` is a frozen public boundary.
- Safe rollback action: Revert the workflow commit and revoke the generated release attestation.
- Authorized offline acceptance command: `./scripts/audit-release-workflow.sh`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Dependabot schedule preference.
