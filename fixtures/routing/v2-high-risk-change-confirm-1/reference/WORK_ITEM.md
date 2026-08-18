# Risk work item: Order-number column expansion

- Risk stratum tag: `migration` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: migration 042 changes int to bigint before the old binary is compatible.
- Plausible blast radius: Rolling deploy can make old instances fail reads and writes.
- Compatibility/rollback constraint: Preserve the v1 DTO and mixed-version operation for 24 hours.
- Approved changed paths: `src/Service.cs`, `src/Migration.cs`, `tests/RiskAcceptanceTests.cs`. `src/Contract.cs` is a frozen public boundary.
- Safe rollback action: Stop new writers, restore the compatibility view, then roll back binaries.
- Authorized offline acceptance command: `dotnet test Orders.sln --filter MixedVersion`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Rename an internal test helper.
