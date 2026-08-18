# bounded-mapping-patch-1: bounded registry update

The consumer contract in `contracts/registry-contract.json` defines the role of `src/claim_properties.json`. Make this one bounded mapping change:

- Add the tenant_id claim mapping to `tenantId`.
- Preserve all existing mappings exactly; do not rename or remove existing keys.
- Edit only `src/claim_properties.json`. Keep the task and contract files unchanged and do not add files.

Validate that the result remains a JSON object with string keys and string values. Key order and whitespace are not significant.
