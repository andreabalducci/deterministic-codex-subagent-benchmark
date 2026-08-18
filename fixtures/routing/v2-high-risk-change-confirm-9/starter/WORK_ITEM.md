# Risk work item: Certificate rotation callback

- Risk stratum tag: `security-compatibility` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: TlsPolicy.cs accepts any certificate when the rotation flag is set.
- Plausible blast radius: A flag intended for dual trust disables server authentication.
- Compatibility/rollback constraint: Trust both pinned public keys without changing HttpClient call sites.
- Approved changed paths: `src/Service.cs`, `src/Migration.cs`, `tests/RiskAcceptanceTests.cs`. `src/Contract.cs` is a frozen public boundary.
- Safe rollback action: Turn off the rotation flag and restore the previous certificate bundle.
- Authorized offline acceptance command: `dotnet test Gateway.sln --filter CertificateRotation`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Rename a health-check tag.
