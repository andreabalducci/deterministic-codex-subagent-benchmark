# Make the TTL cache expire exactly at the deadline and isolate caller mutation

## Repository snapshot

This fixture is a small, self-contained python repository slice. The existing implementation in `src/ttl_cache.py` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/ttl_cache.py` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **deadline equality:** input/condition `now=deadline` must produce `KeyError`.
- **copy in/out:** input/condition `mutate returned dict` must produce `stored dict unchanged`.
- **ttl:** input/condition `zero` must produce `ValueError`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `changeDigest`, `cacheSemantics`, `validationRecord`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
