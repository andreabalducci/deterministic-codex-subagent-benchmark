# Enforce one active external identity per provider in the account migration

## Repository snapshot

This fixture is a small, self-contained repository-artifacts repository slice. The existing implementation in `migrations/004_identity_constraints.sql` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `migrations/004_identity_constraints.sql` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **active duplicate rejected:** input/condition `INSERT INTO identities(account_id,provider,external_id,revoked_at) VALUES (1,'oidc','x',NULL),(2,'oidc','x',NULL)` must produce `reject with an integrity error`.
- **revoked duplicate allowed:** input/condition `INSERT INTO identities(account_id,provider,external_id,revoked_at) VALUES (3,'saml','y','2024-01-01'),(4,'saml','y','2024-02-01')` must produce `accept and commit`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `migrationResult`, `dataInvariant`, `constraintTests`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
