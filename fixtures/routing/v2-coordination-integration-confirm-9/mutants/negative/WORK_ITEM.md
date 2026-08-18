# Work item: Customer-id dual-write rollout

- Topology: `Identity.Contracts -> Customer.Writer -> Customer.Reader`
- Exclusive owners in dependency order: `contract`, `writer`, `reader`
- Ownership: `contract` owns `src/CustomerId.cs`; `writer` owns `src/CustomerRepository.cs`; `reader` owns `src/CustomerLookup.cs` and `tests/CustomerIdRolloutTests.cs`.
- Freeze gate: Freeze CustomerId.cs after worker contract publishes its contract commit.
- Seeded conflict to detect: Reader branch switches before writer populates the new column.
- Authorized acceptance command: `dotnet test Identity.sln --filter CustomerIdRollout`
- Non-goal/distractor: Nullable annotation in a sample.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
