# Make the debounced callback deliver the latest arguments exactly once

## Repository snapshot

This fixture is a small, self-contained react-typescript repository slice. The existing implementation in `src/hooks/debounceCore.ts` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/hooks/debounceCore.ts` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **burst:** input/condition `a,b,c within delay` must produce `c once`.
- **second burst:** input/condition `after fire then d` must produce `d once`.
- **negative delay:** input/condition `-1` must produce `RangeError`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `codeOutcome`, `timerLifecycle`, `deterministicCases`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
