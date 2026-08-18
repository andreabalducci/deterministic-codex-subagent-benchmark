# Routing experiment contract

This document defines the evidence required before the bundled `orchestrate`
skill may describe a model/reasoning pair as an evidence-backed default. The
policy objective is *lowest-cost machine-verified sufficient*: classify the
task family first, then accept the first configuration in the frozen ordinal
cost order that passes its fixed complete-stage gate. The legacy `async-cache-v1`
campaign remains a valid evaluator benchmark, but one coding fixture cannot
establish task-family routing.

## Claims and estimands

The policy-facing worker workflow is a deterministic sequential experiment.
`routing_sequential.py` derives its stages only from the frozen matrix order in
the protocol and `routing_campaign_driver.py` executes only the currently
authorized stage. A configuration is accepted only when it meets the family's
fixed overall-quality floor and its machine and ecosystem boundary. Otherwise
the next configuration is authorized. No human judge, score override, optional
stopping rule, or operator model choice participates in the transition.

The ordinal cost order is frozen by the benchmark operator. It is a routing
order, not a claim about monetary model prices, token rates, subscription
credits, or latency. Results can support a specific pair such as
`gpt-5.6-luna` plus `high`; they must not be interpreted as independent causal
effects of model family or reasoning effort.

The existing complete-treatment matrix remains a separate comparative and
extended analysis. It estimates model/effort configurations under the same
envelope, but it is not sufficient to promote a cheapest-sufficient policy:
promotion consumes terminal, replayable sequential evidence.

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
its sealed fixture evaluator. The worker-family multi-file coordination states
remain a separate estimand and integration oracle; their standalone scores are
never relabeled as evidence of live delegation.

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
runtime manifest, matrix, and machine ID. A sequential protocol-hash revision
invalidates all prior reports, so `machine-a`, `machine-b`, and `machine-c` must
each regenerate their preflight before a new plan is created. Only then may the
planner create the schedule: it commits each report hash and a common normalized capability digest.
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
| `coordination-integration` | Exact contract, producer, consumer, and acceptance state integration across four files |
| `high-risk-change` | Exact compatibility, implementation, rollback, and acceptance state transitions |

The broad historical `sol-high` wording (concurrency, security, migration, and
final review) may be restored only after each named stratum has independent
coverage. Otherwise the generated skill must use the narrower family wording.

Each family requires:

- two development/calibration fixtures excluded from confirmatory analysis;
- at least eight sealed confirmatory fixtures for exploratory evidence;
- twelve sealed confirmatory fixtures for an evidence-backed routing claim;
- fixtures from at least three ecosystems or an explicit ecosystem scope;
- no shared code ancestry that would make fixtures pseudoreplicates;
- a frozen reference, negative/mutant corpus, deterministic evaluator contract, and manifest.

Before a fixture may enter a campaign, calibration must also demonstrate that
semantically equivalent JSON formatting passes, every prohibited state change
fails, and every independently mutable JSON file has its own killed mutant.
Executable fixtures are accepted only by sealed behavioral or data-contract
tests. Read-heavy answers contain exact source locations and excerpts; the two
integration families contain only exact JSON state. No free-form prose is part
of any score.

## Construct-validity authorization gate

Fixture presence and a passing reference/mutant smoke check are not sufficient
to authorize paid generations. `construct_readiness.py` emits a deterministic,
hash-bound report for the frozen protocol and catalog. Promotion requires all
of the following for every claimed family:

- at least six preregistered confirmatory fixtures, three ecosystems, and six distinct task surfaces;
- maximum pairwise prompt trigram Jaccard similarity no greater than 0.85;
- every critical evaluator criterion killed by at least one committed negative mutant;
- at least one evaluator-accepted equivalent positive for every confirmatory fixture;
- a catalog-bound, content-addressed, passing Docker calibration artifact covering every reference, negative mutant, semantic-equivalence positive, and per-state criterion mutant;
- no prose, token-overlap, model-based judge, or human rating in any task outcome.

Generate the diagnostic report with:

```bash
python3 construct_readiness.py calibrate-docker \
  --output runs/routing-docker-calibration.json

python3 construct_readiness.py report \
  --docker-calibration runs/routing-docker-calibration.json \
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

Every worker-family fixture is machine-verifiable. Read-heavy answers are exact
source-bound records; coordination and high-risk fixtures are multi-file state
transitions with frozen semantic JSON outcomes; implementation fixtures execute
sealed behavior. The readiness report therefore depends only on reproducible
machine evidence and never on reviewer agreement.

## Sequential execution state

`routing_campaign.py plan` creates the immutable six-treatment maximum envelope;
`routing_sequential.py` never edits it, creates new run IDs, or changes a
treatment assignment. It derives one stage per family and configuration from
that envelope. The first state authorizes the first (least-cost ordinal) stage
for each family. A complete stage is evaluated only from planned result records:

- the overall pass rate must meet the family's frozen absolute-quality floor;
- every machine pass rate and every ecosystem pass rate must meet the frozen
  floor-minus-shortfall boundary;
- `PASS` and `CANDIDATE_FAILURE` are resolved outcomes; `INFRA_FAILURE` pauses
  the state and cannot be counted or advanced past.

If all conditions hold, the family is terminal with `ACCEPT` and its selected
treatment. If they do not, the exact next stage is authorized. The last failure
is terminal `EXHAUSTED`. These transitions, decisions, source hashes, and result
hashes form a replayable hash chain; no discretionary model selection is
available after initialization.

Use the driver commands in this fixed order after planning:

```bash
python3 routing_campaign_driver.py sequential-init \
  --plan runs/routing-plan.json \
  --sequential-manifest runs/routing-sequential-manifest.json \
  --sequential-state runs/routing-sequential-state.json

# Run on each physical host, only for its currently authorized jobs.
python3 routing_campaign_driver.py run-machine \
  --plan runs/routing-plan.json --machine-id machine-a \
  --preflight runs/routing-preflight-machine-a.json \
  --auth-file /secure/path/benchmark-auth.json \
  --sequential-manifest runs/routing-sequential-manifest.json \
  --sequential-state runs/routing-sequential-state.json

# Run once after all authorized jobs resolve, then repeat run/advance as needed.
python3 routing_campaign_driver.py sequential-advance \
  --plan runs/routing-plan.json \
  --sequential-manifest runs/routing-sequential-manifest.json \
  --sequential-state runs/routing-sequential-state.json

# Only after every family is terminal.
python3 routing_campaign_driver.py sequential-analyze \
  --plan runs/routing-plan.json \
  --sequential-manifest runs/routing-sequential-manifest.json \
  --sequential-state runs/routing-sequential-state.json \
  > runs/routing-sequential-analysis.json
```

The central transition commands have no machine identity; only `run-machine`
and `retry-infra` accept one. After the terminal analysis, publish and verify
the replayable executed prefix:

```bash
python3 routing_sequential_evidence.py publish runs/routing/results/*.json \
  --protocol protocols/routing-operational-v1.json \
  --runtime-manifest protocols/routing-runtime-v1.json \
  --preflight runs/routing-preflight-machine-a.json \
  --preflight runs/routing-preflight-machine-b.json \
  --preflight runs/routing-preflight-machine-c.json \
  --plan runs/routing-plan.json \
  --matrix matrix.json --catalog fixtures/catalog.json \
  --sequential-manifest runs/routing-sequential-manifest.json \
  --terminal-state runs/routing-sequential-state.json \
  --analysis runs/routing-sequential-analysis.json \
  --provenance runs/routing-provenance.json \
  --fixture-root fixtures \
  --candidate-root runs/routing/workspaces \
  --output runs/routing-sequential-evidence

python3 routing_sequential_evidence.py verify \
  runs/routing-sequential-evidence
```

The 648 jobs are the immutable maximum envelope. Sequential execution needs
108 jobs when all six families accept their first stage, 378 under the current
hypothesis ladder, and 648 when every family reaches the final stage. These are
fixed consequences of the registered stage order, not a post-hoc sample-size
choice.

## Distributed execution and infrastructure retries

Run one persistent `routing_campaign_driver.py run-machine` process on each of
the three preregistered physical hosts, using that host's exact preflight report.
The driver walks only its assigned jobs in plan order and safely skips validated
existing results when resumed. A first `INFRA_FAILURE` pauses that machine.
After the operator corrects the external cause, `retry-infra --run-id ...`
archives every current artifact in a numbered immutable attempt directory with
a SHA-256 inventory and then reruns the same planned unit. Candidate failures
are outcomes and are never retried. Do not reuse three logical machine labels on
one host: the machine robustness analysis assumes genuinely distinct hosts.

## Independent samples

An independent sample is a new model generation in a fresh ephemeral session
and workspace. Re-running an evaluator against one generated artifact is a
stability check, not another model sample.

The default operational plan uses six fixtures per family and three fresh
generations per fixture across three machines. Its 648 jobs are an immutable
maximum envelope: six stages for each of six families, with every Williams
order represented once per family on every machine. The policy-facing sequential
run executes a prefix of that envelope only. It requires 108 jobs if every
family accepts stage one, 378 jobs for the current hypothesis ladder, and at
most 648 jobs if all families exhaust the ordered ladder. The six unused
confirmatory fixtures per family are reserved. The complete-treatment extended
protocol uses all twelve fixtures and nine generations, for 3,888 total, when
comparative or publication-grade precision is required. Rank order alone is
never a policy recommendation.

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

## Preregistered cheapest-sufficient decision

For a classified family, the decision is made at each complete stage without looking ahead to later configurations:

1. Evaluate every immutable job authorized for that stage.
2. Compute strict verified-success rates overall and by preregistered machine and fixture ecosystem.
3. Accept exactly when the overall rate reaches the frozen family floor and every machine and ecosystem rate reaches the frozen boundary.
4. Otherwise advance exactly once to the next ordinal-cost stage. At the final stage, record `EXHAUSTED` rather than inventing a new route.

The fixed floors remain `0.80` for routine work, `0.85` for code, and `0.90` for high-risk work. The machine/ecosystem boundary is the family floor minus the frozen maximum quality-floor shortfall. Changing the order, floors, shortfall, family classification, or stage sample changes the protocol and requires a new plan, state chain, and preflights.

The terminal state is accepted only if it replays from the hashes and resolved results of the immutable plan. The policy publication maps each family to its terminal `ACCEPT` treatment (or reports `EXHAUSTED`); it does not choose the highest observed score or rely on a human reviewer.

The legacy complete-treatment analysis continues to report comparative estimates, noninferiority, robustness, and economy/capability contrasts across all six configurations. Those analyses are valuable extended evidence, but they do not replace the sequential terminal-state evidence required to promote the routing policy.

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
- the clean generated `SKILL.md` differs from `SKILL.template.md` rendered with the canonical policy artifact.

Until these gates pass, the skill must identify routes as provisional
hypotheses. This is an integrity constraint, not merely documentation wording.
