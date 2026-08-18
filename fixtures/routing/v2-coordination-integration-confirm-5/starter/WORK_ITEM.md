# Work item: gRPC reservation contract update

- Topology: `Reservation.Proto -> {Inventory.Service, Storefront.Client}`
- Exclusive owners in dependency order: `proto`, `service`, `client`
- Ownership: `proto` owns `src/reservation.proto`; `service` owns `src/ReservationGrpcService.cs`; `client` owns `src/ReservationClient.cs` and `tests/ReservationCompatibilityTests.cs`.
- Freeze gate: Freeze reservation.proto after worker proto publishes its contract commit.
- Seeded conflict to detect: Client branch consumes field 4 before the proto branch reserves it.
- Authorized acceptance command: `dotnet test Inventory.sln --filter ReservationCompatibility`
- Non-goal/distractor: Analyzer warning in an unrelated benchmark.
- Handoff rule: `proto` passes the frozen contract to both branches; `service` and `client` independently return focused results and conflict statements to the integrator.
