# bounded-mapping-patch-7: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/mime_parsers.json`. Make this one bounded mapping change:

- Add text/csv handled by `parse_csv`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/mime_parsers.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
