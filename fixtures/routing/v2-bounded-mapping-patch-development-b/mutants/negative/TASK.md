# bounded-mapping-patch-development-b: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/environment_branches.json`. Make this one bounded mapping change:

- Add the beta environment mapping to `release/beta`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/environment_branches.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
