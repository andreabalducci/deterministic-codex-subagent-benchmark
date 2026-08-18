# bounded-mapping-patch-9: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/column_aliases.json`. Make this one bounded mapping change:

- Add expires_at mapped to `expiresAt`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/column_aliases.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
