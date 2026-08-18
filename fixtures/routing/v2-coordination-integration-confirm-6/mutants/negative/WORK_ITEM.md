# Work item: Cart promotion type consolidation

- Topology: `packages/promo-types -> services/cart-client -> apps/store/cart`
- Exclusive owners in dependency order: `promo-types`, `cart-client`, `cart-ui`
- Ownership: `promo-types` owns `src/promotion.ts`; `cart-client` owns `src/cartClient.ts`; `cart-ui` owns `src/CartSummary.tsx` and `apps/store/cart/CartSummary.test.tsx`.
- Freeze gate: Freeze promotion.ts after worker promo-types publishes its contract commit.
- Seeded conflict to detect: Client and UI branches each define DiscountBreakdown.
- Authorized acceptance command: `pnpm test --filter cart-summary`
- Non-goal/distractor: Footer copyright year.
- Handoff rule: each producer passes its commit/contract note and focused result to the immediate consumer; the last owner reports downstream evidence and unresolved conflicts to the integrator.
