# Canonicalize reverse-proxy route templates without changing parameter tokens

## Repository snapshot

This fixture is a small, self-contained dotnet repository slice. The existing implementation in `src/Gateway.Routing/RouteTemplate.cs` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/Gateway.Routing/RouteTemplate.cs` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **duplicate separators:** input/condition `//Orders///{OrderId}/` must produce `/orders/{OrderId}`.
- **root:** input/condition `/` must produce `/`.
- **blank:** input/condition `   ` must produce `ArgumentException`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `implementation`, `edgeContract`, `verification`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
