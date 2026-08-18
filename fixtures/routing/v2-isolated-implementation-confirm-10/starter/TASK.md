# Merge paged results by identity while accepting server corrections

## Repository snapshot

This fixture is a small, self-contained react-typescript repository slice. The existing implementation in `src/catalog/mergePages.ts` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/catalog/mergePages.ts` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **overlap newer:** input/condition `id 1 v1 + v2` must produce `v2 once`.
- **overlap older:** input/condition `id 1 v3 + v2` must produce `v3 once`.
- **new identity:** input/condition `id 2` must produce `append`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `mergeImplementation`, `versionRule`, `fixtureCases`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
