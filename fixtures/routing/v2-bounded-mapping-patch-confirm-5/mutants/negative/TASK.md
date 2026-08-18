# bounded-mapping-patch-5: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/event_topics.json`. Make this one bounded mapping change:

- Register OrderRefunded as topic `orders.refunded.v1`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/event_topics.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
