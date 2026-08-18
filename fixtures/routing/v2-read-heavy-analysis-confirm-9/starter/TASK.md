# read-heavy-09: exact source-evidence localization

Inspect every visible source and test file before answering. Locate only the executable
statements that directly implement the seeded defect described by this assignment:
`read-heavy-09: Trace a cancellation leak in the document-rendering pipeline`. Nearby declarations, tests, comments, callers, and correctly scoped layers
are context, not defect locations.

Write only `answer.json` with this exact schema and no additional keys:

```json
{"defectLocations":[{"path":"relative/path","line":1,"excerpt":"exact source line"}]}
```

Use repository-relative paths, one-based line numbers, and the complete source line after
trimming surrounding whitespace. Sort records by path, line, then excerpt. Include every
direct defect statement and no benign statement. The evaluator checks the structured
records against the frozen source bytes; prose similarity is never scored.
