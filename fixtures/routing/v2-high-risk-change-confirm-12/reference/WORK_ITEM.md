# Risk work item: Release candidate provenance review

- Risk stratum tag: `final-review-security` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: release manifest references an image digest not produced by the locked build.
- Plausible blast radius: An unreviewed image could be promoted despite passing component tests.
- Compatibility/rollback constraint: Do not rebuild or resign artifacts during final review.
- Approved changed paths: `.github/workflows/change.yml`, `config/rollback.yml`, `scripts/check-risk-change.sh`. `.github/policy.yml` is a frozen public boundary.
- Safe rollback action: Reject the candidate, revoke its approval record, and retain evidence for audit.
- Authorized offline acceptance command: `./scripts/verify-candidate.sh release/candidate.json`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Changelog punctuation.
