# bounded-mapping-patch-2: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/route_labels.json`. Make this one bounded mapping change:

- Add the /audit route label `Audit log`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/route_labels.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
