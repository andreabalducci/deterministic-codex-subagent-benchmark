# Reject stale optimistic acknowledgements in the cart reducer

## Repository snapshot

This fixture is a small, self-contained react-typescript repository slice. The existing implementation in `src/cart/cartReducer.ts` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/cart/cartReducer.ts` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **older ack:** input/condition `r4 then r3` must produce `retain r4`.
- **equal ack:** input/condition `r4 then corrected r4` must produce `apply correction`.
- **new sku:** input/condition `no current line` must produce `insert`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `patchSummary`, `orderingGuarantee`, `testEvidence`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
