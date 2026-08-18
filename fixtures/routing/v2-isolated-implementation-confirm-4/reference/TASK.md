# Harden the release automation permission policy without blocking provenance

## Repository snapshot

This fixture is a small, self-contained repository-artifacts repository slice. The existing implementation in `.github/release-policy.json` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `.github/release-policy.json` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **default deny:** input/condition `defaultPermissions` must produce `{}`.
- **OIDC:** input/condition `releaseJob.id-token` must produce `"write"`.
- **no static secret:** input/condition `allowLongLivedSecrets` must produce `false`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `policyDelta`, `leastPrivilegeContract`, `auditChecks`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
