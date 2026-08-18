# high-risk-10: Service-worker cache schema bump

## Objective

Produce only `answer.json`. Plan a narrowly scoped high-risk change from the deterministic repository and work-item snapshots. The seeded defect is intentionally cross-cutting, but unrelated cleanup is a distractor.

## Required artifact

Return exactly `summary`, `riskAssessment`, `changePlan`, `rollback`, and `acceptance`:

- identify the exact seeded defect, realistic blast radius, compatibility constraint, distractor disposition, and the work item's risk stratum;
- constrain changed paths to the approved implementation/migration/test scope;
- lead with a regression, apply the smallest compatible guard, and rehearse rollback;
- state a deterministic rollback trigger, safe action, and retained compatibility boundary;
- list only the authorized offline acceptance command and required evidence artifacts;
- make final review contingent on defect, rollback, and compatibility evidence, without claiming this one fixture proves broad high-risk family coverage.

Do not repair the distractor, invent production evidence, broaden the change, delete compatibility state, or replace the rollback with a forward-only migration.
