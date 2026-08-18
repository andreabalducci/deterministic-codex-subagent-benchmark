# Routing experiment contract

This document defines the evidence required before the bundled `orchestrate`
skill may describe a model/reasoning pair as an evidence-backed default. The
legacy `async-cache-v1` campaign remains a valid evaluator benchmark, but one
coding fixture cannot establish task-family routing.

## Claims and estimands

The matrix contains complete treatments. Results may support a specific pair
such as `gpt-5.6-luna` plus `high`; they must not be interpreted as independent
causal effects of model family or reasoning effort.

Two experiments are required:

1. **Worker routing.** Give the same fresh-context assignment directly to each
   treatment. This estimates verified worker performance by task family.
2. **Coordinator routing.** Vary the coordinator treatment while holding worker
   treatments, worker prompts, tools, concurrency, and task inputs fixed. This
   estimates decomposition and integration performance. A leaf-worker run is
   not evidence for a coordinator default.

A smaller end-to-end confirmation must then execute the derived policy, because
coordinator and worker choices can interact.

### Live coordinator protocol

The separate coordinator estimand is implemented by
`protocols/coordinator-v1.json`, `coordinator_campaign.py`, and
`coordinator_runner.py`. Its deterministic plan contains 648 generations: six
coordinator treatments × twelve held-out coordination fixtures × nine fresh
replicates, balanced over three machines. Every job carries the same canonical
worker-policy hash. That policy fixes three `gpt-5.6-luna`/`high` leaf workers,
their exact prompts, fresh contexts, spawn depth, and concurrency. Multi-agent
support is enabled only in this experiment.

The live runner timestamps collaboration events as they arrive and records
normalized delegation/worker traces, prompt/model/effort compliance,
interventions, observed and resolved conflicts, critical-path time, worker
utilization, sealed integration acceptance, usage, and runtime/repository
provenance. It fails closed when delegation is inferred only from prose, a
worker prompt or treatment drifts, a worker delegates again, timestamps are
absent, a conflict remains unresolved, or the final integration artifact fails
its sealed fixture evaluator. The existing worker-authored coordination
artifacts remain task inputs and integration oracles; their standalone scores
are never relabeled as live coordinator evidence.

Create a private HMAC key and preregister the immutable plan before any paid
generation:

```console
python3 coordinator_campaign.py validate
python3 coordinator_campaign.py plan --id-key-file /secure/coordinator-id.key --output /secure/coordinator-plan.json
```

The checked-in tests use a fake runtime and incur no model usage. Generating the
confirmatory plan does not authorize running it.

`coordinator_analysis.py` accepts only a complete, resolved 648-result cohort.
Its primary outcome is the conjunction of trace compliance and sealed
integration acceptance. It applies the same paired hierarchical bootstrap,
Holm adjustment, and simultaneous-bound discipline to the preregistered
`sol-medium` coordinator claim, including noninferiority against all five
alternatives, a capability contrast against `terra-medium`, and machine,
ecosystem, and leave-one-fixture-out stability.

Coordinator analysis is intentionally a distinct record kind and estimand. The
current worker routing evidence publisher and policy validator must not consume
`coordinator-analysis` records. Publishing a coordinator evidence bundle and
adding an explicit coordinator-policy evidence reference remain required before
the bundled skill may call `sol-medium` an evidence-backed coordinator default.

The worker experiment fixes the execution envelope across every treatment:
Fast mode/service tier, sandbox, network access, tools, multi-agent availability,
CLI version, prompt, and evaluator image are protocol variables rather than
properties inferred from a model name. In particular, Fast mode is not a
subagent spawn parameter. A campaign may choose the default or priority service
tier, but it must use the same supported tier for every compared treatment and
record the tier actually returned by the runtime. Worker runs disable spawning;
the separate coordinator experiment enables it under a frozen worker policy.

After the protocol is frozen, every registered worker machine must produce its
own authenticated model-list preflight. The report reverse-binds the protocol,
runtime manifest, matrix, and machine ID. Only then may the planner create the
schedule: it commits each report hash and a common normalized capability digest.
The runner rejects a job unless the supplied report matches its assigned-machine
binding, and every v2 result repeats both digests. Publication includes all
reports and verifies the complete chain. This ordering prevents a capability
check from being retrofitted to a plan or protocol after outcomes are known.

## Narrow routing families

The confirmatory protocol uses six narrowly worded families:

| Family | Primary capability |
| --- | --- |
| `mechanical` | Exact semantic-JSON repository edits with immutable-file checks |
| `bounded-mapping-patch` | Small mapping patches accepted by sealed deterministic commands |
| `isolated-implementation` | Isolated implementations accepted by sealed behavioral or data-contract tests |
| `read-heavy-analysis` | Structured defect localization with exact evidence in compact seeded repositories |
| `coordination-integration` | Worker-authored decomposition and integration-plan artifacts, not live delegation |
| `high-risk-change` | Structured risk, change, rollback, and acceptance-plan artifacts in compact seeded repositories |

The broad historical `sol-high` wording (concurrency, security, migration, and
final review) may be restored only after each named stratum has independent
coverage. Otherwise the generated skill must use the narrower family wording.

Each family requires:

- two development/calibration fixtures excluded from confirmatory analysis;
- at least eight sealed confirmatory fixtures for exploratory evidence;
- twelve sealed confirmatory fixtures for an evidence-backed routing claim;
- fixtures from at least three ecosystems or an explicit ecosystem scope;
- no shared code ancestry that would make fixtures pseudoreplicates;
- a frozen reference, negative/mutant corpus, rubric, and manifest.

Before a fixture may enter a campaign, calibration must also demonstrate that
semantically equivalent formatting or prose variants pass, malformed or
additional output fields fail, and every critical criterion is represented in
the evaluator report. Free-form prose is never compared as one exact string:
objective identifiers, paths, commands, evidence locations, ordering, and
required evidence atoms are scored with field-specific deterministic
predicates. The report publishes both a normalized rubric score and a separate
critical-criterion gate.

## Construct-validity authorization gate

Fixture presence and a passing reference/mutant smoke check are not sufficient
to authorize paid generations. `construct_readiness.py` emits a deterministic,
hash-bound report for the frozen protocol and catalog. Promotion requires all
of the following for every claimed family:

- at least 12 confirmatory fixtures, three ecosystems, and ten distinct task surfaces;
- maximum pairwise prompt trigram Jaccard similarity no greater than 0.85;
- every critical evaluator criterion killed by at least one committed negative mutant;
- at least one evaluator-accepted equivalent positive for every confirmatory fixture;
- a catalog-bound, content-addressed, passing Docker calibration artifact covering every reference and mutant;
- for rubric-scored artifacts, two or more blinded raters, at least 12 adjudicated samples per family, agreement of at least 0.80, and no unresolved disagreements.

Generate the diagnostic report with:

```bash
python3 construct_readiness.py calibrate-docker \
  --output runs/routing-docker-calibration.json

python3 construct_readiness.py report \
  --docker-calibration runs/routing-docker-calibration.json \
  --blinded-adjudication runs/routing-blinded-adjudication.json \
  --output runs/routing-construct-readiness.json
```

The command exits non-zero while any family is ineligible but still writes the
full reasons. The planner then requires the passing report:

```bash
python3 routing_campaign.py plan \
  --construct-readiness runs/routing-construct-readiness.json \
  --preflight runs/routing-preflight-machine-a.json \
  --preflight runs/routing-preflight-machine-b.json \
  --preflight runs/routing-preflight-machine-c.json \
  --id-key-file /secure/routing-id.key \
  --output runs/routing-plan.json
```

The current checked-in report is deliberately ineligible. In particular,
`coordination-integration` and `high-risk-change` exceed the prompt-similarity
threshold; all three rubric families lack full critical-criterion mutant
coverage and blinded adjudication. These failures prevent a narrow artifact
benchmark from being presented as evidence for broad real-world coordination,
security, concurrency, or migration claims.

## Independent samples

An independent sample is a new model generation in a fresh ephemeral session
and workspace. Re-running an evaluator against one generated artifact is a
stability check, not another model sample.

The confirmatory target is nine generations for every
fixture/treatment combination, across at least three machines. For six families,
twelve fixtures, nine generations, and six treatments this is 3,888 worker
generations. Development pilots and coordinator experiments are additional.

A smaller campaign is exploratory. Its policy rows remain `hypothesis` or
`inconclusive`, irrespective of rank order.

## Primary and secondary metrics

The primary metric is strict full-task verified success, using every planned
generation as the intent-to-treat denominator. Fixtures receive equal weight so
an easy fixture with many repetitions cannot dominate a family.

Common secondary metrics are:

- structured rubric/behavior score;
- critical-error and unintended-change rates;
- generation latency and evaluator latency;
- input, output, and reasoning tokens when the runtime exposes them;
- dated monetary or credit cost derived from recorded usage;
- tool calls, retries, and infrastructure-failure rate;
- actual model snapshot, service tier, machine, and container identity.

Family-specific metrics include semantic allowed-diff and unintended-file counts
for mechanical work; gold-fact recall and false claims for discovery/review; hidden
behaviors, regressions, and API compatibility for code; and final acceptance,
conflicts, interventions, critical-path time, utilization, and trace compliance
for coordination. Agent count never earns quality credit.

## Preregistered routing decision

Every family has a preregistered candidate route. Analysis compares that
candidate with all five alternatives; it does not choose a winner and invent a
hypothesis afterward.

For each family:

1. Average replicate success within each fixture, then average fixtures.
2. Estimate candidate-minus-comparator contrasts for all alternatives.
3. Use a deterministic hierarchical bootstrap that resamples fixtures and then
   paired trial blocks within fixtures. One-sided bootstrap tests use centered
   deviations (`draw - estimate`) against the preregistered null boundary; raw
   percentile-tail proportions are not reported as p-values.
4. Treat the six absolute-quality gates, 30 candidate-versus-alternative
   noninferiority gates, and six economy/capability gates as one family of 42
   support claims. Apply Holm to all 42 centered-bootstrap p-values, not just
   the pairwise contrasts. Apply a separate Holm procedure to the corresponding
   42 lower-tail contradiction claims.
5. Report Bonferroni simultaneous one-sided 95% bounds across each 42-claim
   family. In analysis schema v2, policy-facing `lower95` and `upper95` are
   these simultaneous bounds; `nominalLower95` and `nominalUpper95` are
   diagnostics only. A gate passes only when both its Holm-adjusted p-value and
   its simultaneous bound exclude the preregistered boundary.
6. Report leave-one-fixture-out sensitivity and treatment-by-fixture
   heterogeneity.

The frozen robustness gate records the ecosystem of every held-out fixture and
reports the candidate quality and preregistered decision contrast separately by
preregistered machine label, ecosystem, omitted fixture, and individual fixture. A
route cannot be `SUPPORTED` when any machine or ecosystem is more than `0.10`
below its absolute-quality floor, when any machine, ecosystem, or
leave-one-fixture-out contrast is more than `0.05` below the required decision
gain, or when the across-fixture candidate-rate or decision-gain range exceeds
`0.75`. These tolerances are conservative first-version guardrails; changing
them creates a new protocol rather than modifying a published campaign.

The checked-in product thresholds are normative and frozen before deblinding:

- simultaneous lower quality bound: `0.80` for routine work, `0.85` for code,
  and `0.90` for high-risk work;
- noninferiority margin: no more than `0.10` below any relevant comparator;
- economy claim: quality noninferiority plus at least `15%` latency or cost
  improvement with the simultaneous lower bound above the preregistered gain;
- capability claim: a costlier route improves success by at least `0.10` over
  the cheapest acceptable route or materially reduces a preregistered critical
  failure;
- the conclusion is stable across ecosystems, machines, and leave-one-fixture-
  out analysis.

Each row resolves to exactly one of:

- `SUPPORTED`: all preregistered gates pass;
- `CONTRADICTED`: a preregistered superiority or safety gate rejects the row;
- `INCONCLUSIVE`: evidence is complete but does not pass either decision gate.

Rank order alone is never a routing recommendation.

## Blinding and stopping

- Freeze and hash the protocol, catalog, fixtures, rubrics, analysis code,
  exclusions, and infrastructure policy before confirmatory generation.
- Use HMAC-derived opaque run IDs and balanced treatment order per fixture.
- Candidate evaluators receive only opaque run IDs and candidate artifacts; they do not receive model mapping, transcript, latency, or usage data. The private execution plan and final statistical analysis necessarily contain the treatment mapping.
- Freeze scores and exclusions before revealing the treatment mapping.
- Use a fixed sample size. One blinded sample-size re-estimation at 50% is
  permitted from pooled outcome and infrastructure rates, up to the registered
  ceiling.
- Pause after three consecutive infrastructure failures or a rolling rate above
  5%. Never replace a sampled candidate failure.
- Retry an unsampled generation only when machine evidence proves no model turn
  began, retaining every attempt record.
- Stop and restart an affected block if a model alias, service behavior, runtime,
  or evaluator image changes.

## Evidence-to-skill gate

Measurement bundles are immutable. A separate routing policy artifact links
each operational default to complete evidence by canonical SHA-256, family,
analysis identifier, treatment, comparators, estimate, and adjusted interval.

The policy validator must reject `evidence-backed` when:

- a referenced bundle is absent, incomplete, invalid, or has a different hash;
- the bundle does not cover the claimed family and coordinator/worker estimand;
- the preregistered decision is not `SUPPORTED`;
- a fixture, protocol, analysis, or score freeze changed after publication;
- the generated `SKILL.md` section differs from the canonical policy artifact.

Until these gates pass, the skill must identify routes as provisional
hypotheses. This is an integrity constraint, not merely documentation wording.
