# Produce stable canonical search URLs without double encoding

## Repository snapshot

This fixture is a small, self-contained react-typescript repository slice. The existing implementation in `src/search/canonicalQuery.ts` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/search/canonicalQuery.ts` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **key order:** input/condition `b=2,a=1` must produce `a=1&b=2`.
- **array order:** input/condition `tag=z,a` must produce `tag=a&tag=z`.
- **reserved:** input/condition `q=a&b` must produce `q=a%26b`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `implementationNote`, `canonicalizationRules`, `caseResults`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
