# bounded-mapping-patch-6: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/feature_components.json`. Make this one bounded mapping change:

- Register billing_v2 with component `BillingWorkspace`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/feature_components.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
