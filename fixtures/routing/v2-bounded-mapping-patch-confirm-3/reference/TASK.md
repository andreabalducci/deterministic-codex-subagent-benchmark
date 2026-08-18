# bounded-mapping-patch-3: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/serializer_aliases.json`. Make this one bounded mapping change:

- Add last_login_at with external alias `lastLoginAt`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/serializer_aliases.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
