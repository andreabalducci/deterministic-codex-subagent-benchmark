# Compute capped exponential retry delays without jitter drift

## Repository snapshot

This fixture is a small, self-contained python repository slice. The existing implementation in `src/retry_policy.py` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/retry_policy.py` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **first:** input/condition `[1,2,30]` must produce `2`.
- **fourth:** input/condition `[4,2,30]` must produce `16`.
- **cap:** input/condition `[8,2,30]` must produce `30`.
- **zero attempt:** input/condition `[0,2,30]` must produce `raise ValueError`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `repairSummary`, `backoffDefinition`, `executedExamples`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
