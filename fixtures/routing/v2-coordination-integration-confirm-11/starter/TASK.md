# Integration checkpoint: Async job-result contract

Resolve the repository's split-brain state: contract, producer, consumer, and acceptance must converge on one frozen boundary.

Read `WORK_ITEM.md` and `repo.snapshot.json`, then edit exactly the four existing JSON
documents below `integration/`. Keep every supplied scenario, owner, path, conflict,
distractor, and command string byte-for-byte; change only the state fields required by the
work item. The accepted repository freezes contract version 2, makes both consumers use
version 2 and `canonical-lowercase`, follows the declared owner order, resolves
`Executor returns error_detail while API reads error`, rejects `Formatter upgrade proposal`, and finishes as
`accepted`.

Do not write an explanation or add files. Semantic JSON equality across all four files is
the sole score: partial merges, alternate commands, stale versions, or unresolved state fail.
