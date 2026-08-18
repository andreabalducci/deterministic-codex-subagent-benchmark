# Make the container health contract match the non-root runtime endpoint

## Repository snapshot

This fixture is a small, self-contained repository-artifacts repository slice. The existing implementation in `deploy/runtime-contract.json` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `deploy/runtime-contract.json` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **non-root:** input/condition `user` must produce `65532`.
- **port:** input/condition `listenPort` must produce `8080`.
- **probe path:** input/condition `readiness.path` must produce `"/health/ready"`.
- **drain:** input/condition `graceSeconds` must produce `15`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `artifactUpdate`, `runtimeContract`, `deploymentAssertions`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
