# bounded-mapping-patch-4: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/team_owners.json`. Make this one bounded mapping change:

- Add mobile ownership mapped to `@mobile-platform`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/team_owners.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
