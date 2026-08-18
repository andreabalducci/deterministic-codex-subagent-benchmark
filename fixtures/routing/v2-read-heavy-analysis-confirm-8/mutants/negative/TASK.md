# Explain why the local API starts before its database is usable

## Assignment

Read the entire repository snapshot before answering. Produce only `answer.json`; do not modify source or configuration. Identify the exact architectural or defect facts requested, connect them across files where necessary, and distinguish seeded distractors from causal evidence.

## Evidence rules

- Every reported fact must cite a repository-relative path and an exact 1-based line number.
- The cited line must contain the supplied excerpt verbatim after trimming whitespace.
- Do not infer a vulnerability or failure from filenames alone.
- Report all and only the material facts supported by this snapshot.

## Output contract

Return one JSON object with exactly these top-level fields: `environmentDiagnosis`, `composeFaults`, `configurationEvidence`, `irrelevantSignals`. Facts and evidence must remain structured arrays; prose without path/line evidence is not accepted.
