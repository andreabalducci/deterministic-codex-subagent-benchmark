# Parse quoted CSV fields while preserving empty trailing columns

## Repository snapshot

This fixture is a small, self-contained dotnet repository slice. The existing implementation in `src/Imports/CsvRowParser.cs` is intentionally defective; neighboring files define the API and deployment assumptions and must remain unchanged.

## Objective

Repair `src/Imports/CsvRowParser.cs` and complete `answer.json`. Do not rename public APIs or edit any other file. The source change, not the report alone, is the deliverable.

## Acceptance contract

- **quoted comma:** input/condition `a,"b,c",d` must produce `3 fields`.
- **escaped quote:** input/condition `"a""b"` must produce `a"b`.
- **trailing empty:** input/condition `a,b,` must produce `3 fields`.
- Preserve the supplied public surface and keep the implementation deterministic.
- `answer.json` must contain exactly these top-level rubric fields: `parserChange`, `grammarContract`, `scenarioChecks`.

## Sealed evaluation

The benchmark keeps its evaluator and cases outside the worker workspace. A command adapter can execute `python3 sealed/tests/evaluate_candidate.py <candidate-root>` from the fixture root. The visible prompt does not disclose reference source or expected report values.
