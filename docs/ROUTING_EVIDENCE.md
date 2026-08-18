# Routing evidence bundles

`routing_evidence.py` publishes the complete public input to a routing decision as a portable,
deterministic directory. A valid bundle includes the frozen protocol and runtime controls, plan, treatment matrix,
one reverse-bound capability preflight report per campaign machine,
task catalog and every catalog manifest, allow-listed candidate files, sealed evaluator inputs, a
sanitized result cohort, replayed analysis, execution provenance, and a SHA-256 audit inventory.

Raw transcripts, credentials, and result fields outside the public fields in
`routing_campaign.RESULT_V2_KEYS` are not copied. Candidate worktrees are reduced to the exact files
allow-listed by each fixture contract; fixture references and mutants are excluded. Sealed evaluator
inputs are included so verification can independently recompute candidate hashes and every resolved
PASS/FAIL status. A cohort containing `INFRA_FAILURE` is rejected rather than publishing a score that
cannot be replayed from candidate/evaluator evidence. Evidence publication rejects legacy v1 results. The v2 public fields retain hashes
and model, usage, evaluator, and execution provenance, but never transcript content. Source result documents are represented only
by SHA-256 hashes in the sanitization audit. The verifier rejects symlinks, non-canonical JSON,
untracked files, missing files, digest mismatches, catalog/manifest drift, incomplete cohorts, and
analysis output that cannot be reproduced from the packaged results.

## Publish

Create a provenance document conforming to `schemas/routing-provenance.schema.json`. In particular,
its configuration list and machine IDs must exactly match the protocol, its evaluator image must be
content-addressed by a registry digest or immutable image ID, and `analysisImplementation.sha256` must identify the exact checked-in
`routing_campaign.py` used for publication. Publication also requires a clean Git revision so that
the runner and evaluator identified by provenance can be retrieved from the recorded commit.

```sh
python3 routing_evidence.py publish runs/routing/*.json \
  --plan runs/routing-plan.json \
  --preflight runs/routing-preflight-machine-a.json \
  --preflight runs/routing-preflight-machine-b.json \
  --preflight runs/routing-preflight-machine-c.json \
  --analysis runs/routing-analysis.json \
  --provenance runs/routing-provenance.json \
  --candidate-root runs/routing/workspaces \
  --output runs/published/routing-v1
```

Publication refuses to overwrite a non-empty directory. The generated directory is deterministic
when all supplied inputs are identical.

## Verify and replay

```sh
python3 routing_evidence.py verify runs/published/routing-v1
```

Verification first reconstructs each allow-listed candidate and fixture, re-runs its sealed evaluator
in the pinned evaluator image where required, and rejects any status or candidate-hash mismatch. It
then re-runs `routing_campaign.analyze` and compares its canonical output with the packaged analysis.
It reports both the byte digest of `bundle.json` and the canonical JSON object digest.
`evidenceBundles[].canonicalSha256` in a routing policy must pin the latter, matching
`routing_policy.canonical_sha256` and remaining independent of the file's trailing newline.

## Policy integration contract

An evidence-backed policy entry should resolve its evidence path to the bundle directory, not
directly to `analysis.json`. The policy validator should then call
`routing_evidence.verify_bundle`, select the route's `familyId` from the verified bundle decisions,
and read its estimates from the bundle's verified `analysis.json`. This prevents an analysis file
from being cited without its protocol, cohort, fixture manifests, provenance, and replay audit.
The bundle, policy metadata, evidence reference, and route also carry an explicit `estimand`.
Worker-artifact evidence cannot therefore promote a live-coordinator route.

The repository policy remains provisional until the required real campaign is complete and a route
has a `SUPPORTED` decision in such a verified bundle.
Promotion is also fail-closed on construct validity. Pass the hash-bound report
to `routing_policy.py --construct-readiness <report>`; each evidence-backed
worker route must name a family whose report entry is individually eligible.
A strong scoped family may therefore be promoted without pretending that an
ineligible broad family has become valid.

## Live-coordinator evidence

Live coordination is a different estimand and therefore uses
`coordinator_evidence.py`, not the worker publisher above. Its literal estimand is
`live-coordinator-with-frozen-workers`: the coordinator treatment varies while the
protocol-bound Luna-high leaf policy, worker count, concurrency, prompts, and spawn depth remain
fixed. A coordinator bundle contains the protocol, balanced plan, bound catalog and twelve fixture
manifests, the complete resolved 648-result cohort, clean-revision provenance, the preregistered
analysis, and a canonical SHA-256 audit inventory. Verification revalidates every result and
recomputes `coordinator_analysis.analyze` from the packaged cohort.

```sh
python3 coordinator_evidence.py publish runs/coordinator/results/*.json \
  --plan runs/coordinator-plan.json \
  --analysis runs/coordinator-analysis.json \
  --provenance runs/coordinator-provenance.json \
  --output runs/published/coordinator-v1

python3 coordinator_evidence.py verify runs/published/coordinator-v1
```

The policy stores this as a separate `coordinatorDefaults` session-start claim. Spawned-worker
routes in `defaults` are required to retain `estimand: worker`; a coordinator bundle cannot promote
the worker `sol-medium` row. Conversely, a worker bundle cannot support the live-coordinator claim.
This distinction also reflects the runtime boundary: `spawn_agent` selects a leaf worker but cannot
change the model of the already-running parent session.
