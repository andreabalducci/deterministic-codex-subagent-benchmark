# bounded-mapping-patch-10: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/icon_registry.json`. Make this one bounded mapping change:

- Register archived with icon `Archive`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/icon_registry.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
