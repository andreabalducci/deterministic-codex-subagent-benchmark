# Safety transition: Online index migration

Apply a risk-gated transition: preserve the frozen boundary, add only the approved guard, rehearse rollback, then evaluate acceptance.

Use `WORK_ITEM.md` and `repo.snapshot.json` as immutable evidence. Update exactly the four
existing JSON documents under `change/`, preserving their schemas and all supplied defect,
constraint, scope, command, trigger, action, and compatibility strings. The final state must
preserve the frozen boundary, make mixed-version operation safe, add the seeded regression
and smallest compatible guard, leave the distractor unchanged, record a rehearsed non-forward-
only rollback, pass regression/compatibility/rollback, and set review to `approved`.

No prose is graded and no new artifact is allowed. The evaluator compares the complete
semantic state; skipped reversal, widened scope, premature approval, or evidence edits fail.
