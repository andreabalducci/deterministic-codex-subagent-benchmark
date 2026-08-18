# Work item: Generated locale-key adoption

- Topology: `locales/schema -> generated keys -> account settings UI`
- Exclusive owners in dependency order: `locale-schema`, `generator`, `account-ui`
- Ownership: `locale-schema` owns `src/schema.ts`; `generator` owns `src/generatedKeys.ts`; `account-ui` owns `src/LocalePicker.tsx` and `apps/account/LocalePicker.test.tsx`.
- Freeze gate: Freeze schema.ts after worker locale-schema publishes its contract commit.
- Seeded conflict to detect: Generator and UI branches both modify generatedKeys.ts.
- Authorized acceptance command: `pnpm test --filter locale-picker`
- Non-goal/distractor: Missing translation for an out-of-scope marketing page.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
