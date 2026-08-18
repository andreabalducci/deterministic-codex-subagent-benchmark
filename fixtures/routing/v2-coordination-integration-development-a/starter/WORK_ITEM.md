# Work item: Invoice export contract split

- Topology: `Billing.Contracts -> Billing.Api -> Billing.Worker`
- Exclusive owners in dependency order: `contract`, `api`, `worker`
- Ownership: `contract` owns `src/ExportRequest.cs`; `api` owns `src/ExportController.cs`; `worker` owns `src/InvoiceExportJob.cs` and `tests/Billing.IntegrationTests.cs`.
- Freeze gate: Freeze ExportRequest.cs after worker contract publishes its contract commit.
- Seeded conflict to detect: The worker still reads the frozen v1 ExportRequest shape.
- Authorized acceptance command: `dotnet test Billing.sln --filter InvoiceExport`
- Non-goal/distractor: README typo in the reporting sample.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
