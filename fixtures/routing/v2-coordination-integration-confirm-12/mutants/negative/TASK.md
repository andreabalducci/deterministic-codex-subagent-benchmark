# Integration checkpoint: Helm environment-key convergence

Use the ownership graph as the merge schedule. Complete each state handoff and leave a fully accepted, conflict-free integration record.

Read `WORK_ITEM.md` and `repo.snapshot.json`, then edit exactly the four existing JSON
documents below `integration/`. Keep every supplied scenario, owner, path, conflict,
distractor, and command string byte-for-byte; change only the state fields required by the
work item. The accepted repository freezes contract version 2, makes both consumers use
version 2 and `canonical-lowercase`, follows the declared owner order, resolves
`Entrypoint and chart use different names for the same timeout`, rejects `Development-only compose port`, and finishes as
`accepted`.

Do not write an explanation or add files. Semantic JSON equality across all four files is
the sole score: partial merges, alternate commands, stale versions, or unresolved state fail.
