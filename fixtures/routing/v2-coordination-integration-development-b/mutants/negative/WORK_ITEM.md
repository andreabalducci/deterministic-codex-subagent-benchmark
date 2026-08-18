# Work item: Saved-search query key rollout

- Topology: `packages/query-model -> {web/search, web/saved-searches}`
- Exclusive owners in dependency order: `model`, `search-ui`, `saved-search-api`
- Ownership: `model` owns `src/queryKey.ts`; `search-ui` owns `src/SearchPage.tsx`; `saved-search-api` owns `src/savedSearchApi.ts` and `web/search/SearchPage.test.tsx`.
- Freeze gate: Freeze queryKey.ts after worker model publishes its contract commit.
- Seeded conflict to detect: Both UI branches edit the query-key adapter.
- Authorized acceptance command: `pnpm test --filter saved-search`
- Non-goal/distractor: Unrelated Storybook button padding.
- Handoff rule: `model` passes the frozen contract to both branches; `search-ui` and `saved-search-api` independently return focused results and conflict statements to the integrator.
