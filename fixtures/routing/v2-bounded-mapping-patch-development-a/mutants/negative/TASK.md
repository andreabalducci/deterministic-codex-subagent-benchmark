# bounded-mapping-patch-development-a: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/http_status_handlers.json`. Make this one bounded mapping change:

- Add the 409 status mapping to `conflict`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/http_status_handlers.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
