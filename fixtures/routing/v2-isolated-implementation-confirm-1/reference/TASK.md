# Fix the sliding-window limiter boundary and zero-capacity contract

## Repository snapshot

This fixture is a small, self-contained dotnet repository slice. The existing implementation in `src/Traffic/SlidingWindowLimiter.cs` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/Traffic/SlidingWindowLimiter.cs` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **capacity:** input/condition `third request at capacity 2` must produce `reject`.
- **boundary:** input/condition `age equals window` must produce `evict`.
- **invalid capacity:** input/condition `zero` must produce `throw`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `fix`, `boundaryBehavior`, `proof`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
