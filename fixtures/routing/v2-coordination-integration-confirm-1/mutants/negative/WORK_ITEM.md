# Work item: Outbox envelope integration

- Topology: `Messaging.Contracts -> Checkout.Api -> Outbox.Dispatcher`
- Exclusive owners in dependency order: `contract`, `producer`, `dispatcher`
- Ownership: `contract` owns `src/OutboxEnvelope.cs`; `producer` owns `src/CheckoutEndpoint.cs`; `dispatcher` owns `src/OutboxPump.cs` and `tests/OutboxContractTests.cs`.
- Freeze gate: Freeze OutboxEnvelope.cs after worker contract publishes its contract commit.
- Seeded conflict to detect: Producer and dispatcher assume different header casing.
- Authorized acceptance command: `dotnet test Commerce.sln --filter Outbox`
- Non-goal/distractor: Legacy email sender TODO.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
