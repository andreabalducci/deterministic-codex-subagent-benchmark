# Reject archive traversal after normalization on every platform

## Repository snapshot

This fixture is a small, self-contained python repository slice. The existing implementation in `src/archive_paths.py` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/archive_paths.py` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **posix traversal:** input/condition `../secret` must produce `ValueError`.
- **windows traversal:** input/condition `..\secret` must produce `ValueError`.
- **drive:** input/condition `C:\secret` must produce `ValueError`.
- **safe:** input/condition `images/a.png` must produce `PurePosixPath`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `hardeningOutcome`, `normalizationModel`, `maliciousInputs`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
