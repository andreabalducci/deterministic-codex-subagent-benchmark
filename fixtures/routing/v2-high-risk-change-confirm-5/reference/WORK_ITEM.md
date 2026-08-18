# Risk work item: Money precision transition

- Risk stratum tag: `migration` (a sampling stratum, not a coverage claim).
- Deterministic seeded defect: Price.Amount is decimal but migration 118 creates real and rounds refunds.
- Plausible blast radius: Refund totals can drift from captured payment amounts.
- Compatibility/rollback constraint: Keep JSON amounts as strings and support old rows during backfill.
- Approved changed paths: `src/Service.cs`, `src/Migration.cs`, `tests/RiskAcceptanceTests.cs`. `src/Contract.cs` is a frozen public boundary.
- Safe rollback action: Pause backfill, restore decimal shadow reads, and leave new columns intact.
- Authorized offline acceptance command: `dotnet test Payments.sln --filter MoneyPrecision`
- Required acceptance artifacts: the focused regression result, a rollback rehearsal record, and a compatibility review note.
- Distractor/non-goal: Upgrade a logging package.
