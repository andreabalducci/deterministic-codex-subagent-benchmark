# Risk work item: Pull-request workflow hardening

- Risk stratum tag: `security` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: pr-preview.yml checks out fork code while holding cloud credentials.
- Plausible blast radius: Untrusted pull request code can exfiltrate preview credentials.
- Compatibility/rollback constraint: Keep fork previews available without secrets and preserve maintainer previews.
- Approved changed paths: `.github/workflows/change.yml`, `config/rollback.yml`, `scripts/check-risk-change.sh`. `.github/policy.yml` is a frozen public boundary.
- Safe rollback action: Disable the preview workflow and delete the temporary cloud role session.
- Authorized offline acceptance command: `./scripts/lint-workflow-trust.sh .github/workflows/pr-preview.yml`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Issue template wording.
