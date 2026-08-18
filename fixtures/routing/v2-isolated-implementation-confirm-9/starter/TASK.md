# Match OAuth scopes as tokens rather than substrings

## Repository snapshot

This fixture is a small, self-contained dotnet repository slice. The existing implementation in `src/Auth/ScopeMatcher.cs` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/Auth/ScopeMatcher.cs` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **exact:** input/condition `read write + write` must produce `true`.
- **prefix:** input/condition `orders.read + orders` must produce `false`.
- **spacing:** input/condition `read   write + write` must produce `true`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `securityFix`, `tokenizationContract`, `attackCases`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
