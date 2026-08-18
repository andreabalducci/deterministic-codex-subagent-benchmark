# Payroll timezone normalization: controlled-change assignment

Plan the regression-first repair as if a release approver will reject any unsupported production claim.

Use only `repo.snapshot.json` and `WORK_ITEM.md`. The deterministic signal to
explain is `payroll.py treats naive timestamps as UTC although stored rows are local office time`. `Simplify a fixture factory` is intentionally unrelated. This is a
planning/evidence task for a compact python snapshot; do not
claim that code was changed, deployed, or observed in production.

Return only `answer.json`. Its exact sections are:

- `summary`: a bounded description of the proposed repair;
- `riskAssessment`: risk stratum, seeded defect, blast radius, compatibility
  constraints, and disposition of the distractor;
- `changePlan`: approved path scope and regression-first ordered steps;
- `rollback`: deterministic trigger, safe actions, and the compatibility state
  that must survive reversal;
- `acceptance`: only the authorized offline command, required evidence
  artifacts, and a final-review condition tied to defect, rollback, and
  compatibility proof.

Keep frozen boundaries unchanged. Do not widen the approved paths, replace
rollback with a forward-only migration, repair the distractor, or manufacture
operational evidence.
