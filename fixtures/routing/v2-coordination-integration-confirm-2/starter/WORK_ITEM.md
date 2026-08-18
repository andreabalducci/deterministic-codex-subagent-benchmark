# Work item: Session-state boundary extraction

- Topology: `packages/session-core -> {apps/portal/auth, apps/admin/auth}`
- Exclusive owners in dependency order: `session-core`, `portal`, `admin`
- Ownership: `session-core` owns `src/sessionState.ts`; `portal` owns `src/PortalGate.tsx`; `admin` owns `src/AdminGate.tsx` and `apps/portal/auth/session.test.tsx`.
- Freeze gate: Freeze sessionState.ts after worker session-core publishes its contract commit.
- Seeded conflict to detect: Both applications currently declare SessionStatus.
- Authorized acceptance command: `pnpm vitest run session`
- Non-goal/distractor: Deprecated icon import.
- Handoff rule: `session-core` passes the frozen contract to both branches; `portal` and `admin` independently return focused results and conflict statements to the integrator.
