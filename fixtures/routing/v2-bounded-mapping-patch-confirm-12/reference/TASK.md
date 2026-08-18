# bounded-mapping-patch-12: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/release_channels.json`. Make this one bounded mapping change:

- Add lts mapped to `refs/heads/release/lts`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/release_channels.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
