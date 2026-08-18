# bounded-mapping-patch-8: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/runner_labels.json`. Make this one bounded mapping change:

- Add linux-arm64 mapped to `ubuntu-24.04-arm`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/runner_labels.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
