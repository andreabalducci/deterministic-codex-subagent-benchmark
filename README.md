# Deterministic Codex subagent benchmark

This repository defines a benchmark protocol for comparing six Codex model/effort configurations on an immutable .NET concurrency task. It separates stochastic code generation from reproducible evaluation, balances execution order across machines, and keeps the configuration mapping blinded during scoring.

The planned campaign treats the six configurations as complete treatments. The matrix is intentionally not factorial, so any future results must not be interpreted as independent causal estimates of either model choice or reasoning effort.

## Evidence status

The protocol and fixture are implemented, but no benchmark campaign has been run or published from this repository. Consequently, there is currently no public campaign evidence that substantiates any of the six routing rows in the bundled `orchestrate` skill.

Keep these states distinct:

- **Protocol evidence:** committed source, manifest validation, unit tests, and trusted reference/mutant calibration show that the evaluator and its fixture behave as specified.
- **Campaign evidence:** independently generated, planned jobs and their immutable result records are needed to compare configurations. None are published yet.
- **Published evidence:** a reviewable archive of a frozen campaign, its provenance, aggregate analysis, and the mapping disclosure. None exists yet.

## Reproducibility contract

- The fixture, prompt, candidate, harness, schema, SDK, Dockerfiles, lockfile, and container identities are hashed in the result provenance.
- The .NET SDK and both base images are digest-pinned; the Codex CLI is lockfile-pinned.
- Concurrency tests use explicit gates rather than sleeps or timing assertions. Time-dependent behavior uses a manual `TimeProvider`.
- A seeded Williams design balances order position and first-order carryover for all six configurations. Machine blocks are deterministically interleaved.
- Run IDs are HMAC-derived with a local secret key. The public scoring plan contains no model mapping.
- Each independently generated artifact is tested once publicly and repeatedly against the hidden suite. Repetitions test runtime stability; they are not additional model samples.
- First timeouts are rerun at twice the limit. Confirmed candidate-execution timeouts are candidate failures; build, launcher, and timeout-then-success anomalies are infrastructure failures that must be replaced before official aggregation.
- Every materialized build uses the repository's source-clearing `NuGet.config`; evaluation never depends on an external package feed.
- The evaluator caps process output and container CPU, memory, PID count, filesystem access, privileges, and network access.

Model generation itself is not deterministic because Codex does not expose a generation seed. Statistical reproducibility therefore comes from a preregistered plan and multiple independent generations, not from expecting byte-identical candidates.

## Layout

```text
fixtures/async-cache-v1/
  starter/       worker-visible source and public tests
  hidden/        evaluator-only-at-run-time tests (source is versioned here)
  reference/     known-good implementation
  mutants/       known-bad implementations that must be rejected
docker/          pinned generator and evaluator images
runs/            ignored plans, keys, workspaces, logs, and results
schemas/         result contract
skills/          bundled, installable orchestration skill
harness.py       planning, generation, evaluation, and aggregation
```

## Bundled orchestration skill

The repository includes the exact [`orchestrate`](skills/orchestrate/SKILL.md) routing policy used to design and audit this benchmark. It keeps the benchmark and its recommended multi-agent operating model versioned together.

Install it for Codex:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/orchestrate"
cp -R skills/orchestrate/. "${CODEX_HOME:-$HOME/.codex}/skills/orchestrate/"
```

Restart Codex after installation, then invoke it explicitly with `$orchestrate` or allow implicit selection for substantial multi-agent work. The skill includes UI metadata and has no external tool dependencies.

This is an expanded adaptation of Eric Provencher's original [`orchestrate` skill](https://github.com/provencher/codex-skills/tree/main/orchestrate), distributed under its included MIT license. The routing profile adds model-specific hypotheses, Fast mode clarification, context-isolation rules, and coordinator-side verification. Its six routing rows are provisional until a published campaign substantiates them.

## Prerequisites

- Docker
- Python 3.12+
- .NET SDK 10.0.301 only for trusted native fixture verification
- A dedicated benchmark Codex credential file

Do not pass your persistent default Codex login to generated code. Create a short-lived, least-privilege credential exclusively for the benchmark and provide it with `--auth-file` or `CODEX_BENCH_AUTH_FILE`. Codex itself requires outbound access to OpenAI, while model-generated commands run under the workspace sandbox without automatic approval. The container limits exposure but cannot make a credential invisible to code executing in the same container; strong adversarial operation requires an egress allowlist and credential broker outside this harness.

## Quick start

Start with the generation-free checks. These validate the fixture and evaluator without making model requests:

```bash
python3 harness.py manifest
python3 -m unittest discover -s tests -v
python3 harness.py verify --backend native --repeat 1 \
  --output runs/verification-native.json
python3 harness.py verify --backend docker --repeat 1 \
  --output runs/verification-docker.json
```

Use `--repeat 20` for the full trusted-fixture calibration described below. The native check requires .NET SDK 10.0.301; the Docker check builds the pinned evaluator image automatically when needed.

Running a benchmark campaign makes paid Codex model requests. Before starting, choose the campaign size and machine labels, prepare a dedicated short-lived credential, and freeze those choices. This example creates the documented 90-sample-per-configuration plan across three machines:

```bash
python3 harness.py plan \
  --trials 90 \
  --seed benchmark-v1 \
  --machines mac-a,linux-b,mac-c \
  --output runs/plan.json \
  --blinded-output runs/plan.blinded.json \
  --id-key-file runs/id-key

export CODEX_BENCH_AUTH_FILE=/secure/path/benchmark-auth.json
```

Keep `runs/plan.json`, `runs/id-key`, and the credential private. On each machine, process its jobs in plan order using the exact assigned `runId` and `machineId` from `runs/plan.json`:

```bash
python3 harness.py run-job \
  --plan runs/plan.json \
  --run-id <assigned-run-id> \
  --machine-id <assigned-machine-id> \
  --backend docker \
  --repeat 20
```

After every planned job has a resolved result, aggregate and publish the sanitized evidence bundle:

```bash
python3 harness.py aggregate \
  --plan runs/plan.json \
  runs/results/*.json \
  --output runs/summary.json

python3 harness.py publish \
  --plan runs/plan.json \
  runs/results/*.json \
  --output runs/evidence.json
```

The commands below explain calibration, planning, execution, replacement handling, aggregation, and publication in detail. Do not treat the generation-free CLI flag check as proof that a model is available to the benchmark credential.

## Calibrate the evaluator

```bash
python3 harness.py manifest
python3 -m unittest discover -s tests -v
python3 harness.py verify --backend native --repeat 20
python3 harness.py verify --backend docker --repeat 20
```

`native` is deliberately available only to the committed trusted reference and mutants. External candidates are always evaluated in Docker. Calibration succeeds only if the reference passes every repetition and all nine mutants are rejected.

## Continuous integration and generation-free CLI flag preflight

Every push and pull request runs manifest validation, the harness unit suite, and one native trusted-fixture verification on Ubuntu. It also builds the pinned generator image and asks its installed, lockfile-pinned Codex CLI to parse each configured `--model` and `model_reasoning_effort` pair with `codex exec --help`. This is a CLI flag/configuration compatibility check only: it makes no model request, uses no credential, and does not validate model availability or generate a candidate.

Model availability must be checked separately with an authenticated, generation-free model-list preflight supported by the pinned CLI/API. That check is not implemented in this workflow because it requires a credential; it must not be inferred from the flag check.

The expensive multi-platform native calibration and pinned Docker evaluator calibration remain manual and use no paid generations:

```bash
gh workflow run verify.yml -f repeat=20
```

## Create a blinded, balanced plan

`--trials` is the number of six-configuration blocks and must be divisible by `6 × machine count`. Size the campaign before generation. For example, a conservative two-proportion approximation for detecting a 0.60 versus 0.85 pass rate with 80% power and family-wise alpha 0.05 across 15 pairwise comparisons requires 90 samples per configuration:

```bash
python3 harness.py power --baseline-rate 0.60 --target-rate 0.85
```

With three machines, 90 trials produce 540 independent generations and five complete balance cycles per machine.

```bash
python3 harness.py plan \
  --trials 90 \
  --seed benchmark-v1 \
  --machines mac-a,linux-b,mac-c \
  --output runs/plan.json \
  --blinded-output runs/plan.blinded.json \
  --id-key-file runs/id-key
```

Keep `runs/plan.json` and `runs/id-key` private until scoring is frozen. Give evaluators only `runs/plan.blinded.json` and anonymous artifacts. The seed reproduces scheduling, but HMAC IDs are reproducible only with the same secret key. It does not seed model generation.

## Execute assigned jobs

Run jobs in plan order under their assigned logical machine label. The harness rejects a mismatched declared label and validates every same-machine predecessor result before continuing. A label is not cryptographic proof of physical host identity; use per-host signing credentials when that is part of the threat model.

```bash
python3 harness.py run-job \
  --plan runs/plan.json \
  --run-id <opaque-run-id> \
  --machine-id mac-a \
  --auth-file /secure/path/benchmark-auth.json \
  --backend docker \
  --repeat 20
```

For each job, the harness:

1. Materializes only the task, starter code, and public tests in an opaque workspace.
2. Runs the pinned Codex configuration in a resource-limited generator container.
3. Builds and runs the public suite before hidden test source exists in that workspace.
4. Copies only the candidate assembly into a separate hidden-test build workspace.
5. Publishes the hidden runner, then executes only published binaries in a read-only, networkless container.
6. Writes a provenance-rich result to `runs/results/<run-id>.json`.

Generation timeouts, output-limit failures, and nonzero exits are recorded as resolved candidate failures, so they cannot be resampled. `--replace` is restricted to an existing post-generation infrastructure-failure result: it archives that record, verifies the retained candidate and generation metadata against it, and re-evaluates the same generated artifact without invoking Codex again. A generator launcher failure produced no sample and is deliberately non-replaceable; freeze the campaign and follow its preregistered infrastructure-failure policy instead of silently drawing another generation.

## Evaluate an external candidate

```bash
python3 harness.py evaluate \
  --candidate /path/to/opaque-candidate \
  --run-id 0123456789abcdef \
  --model gpt-5.6-luna \
  --effort high \
  --machine mac-a \
  --backend docker \
  --repeat 20
```

External evaluations default to `runs/external-results/<run-id>.json`, cannot write into the campaign result namespace, and refuse to replace an existing report.

## Aggregate a complete cohort

```bash
python3 harness.py aggregate \
  --plan runs/plan.json \
  runs/results/*.json \
  --output runs/summary.json
```

Official aggregation validates plan membership and hashes, rejects duplicate run IDs, and refuses missing or unresolved runs. `--allow-incomplete` is for diagnostics only. The summary reports pass rate, Wilson 95% interval, status counts, per-machine counts, and median/IQR for generation and evaluation duration. Duplicate candidate hashes are flagged. Raw evaluation and verification reports include captured test output and are written mode `0600`; use the sanitized publication command for public evidence.

## Publish a frozen evidence bundle

After every planned run has a resolved result, publish a deblinded, reviewable bundle:

```bash
python3 harness.py publish \
  --plan runs/plan.json \
  runs/results/*.json \
  --output runs/evidence.json
```

The command refuses incomplete or unresolved cohorts. It emits the frozen plan, fixture manifest, matrix, deblinded mapping, intent-to-treat aggregate, structured behavior outcomes, repository provenance, queue/replacement audit records, hashes of the source results, and sanitized per-run evidence. Missing later behaviors are explicit `NOT_RUN`; duplicate or contradictory behavior markers are `AMBIGUOUS`. The output is checked against the strict nested contract in `schemas/evidence-bundle.schema.json`. Captured stdout and stderr—including hidden assertion text—are replaced by byte counts and SHA-256 hashes. The bundle contains no credential or generation transcript; archive it immutably alongside the trusted calibration reports, publish its digest, and update routing rows only where that evidence supports the conclusion.

## Benchmark expansion required for routing claims

The current fixture is one isolated concurrency-repair task. Its generator prompt explicitly says “Complete the work directly without spawning agents,” so it does not test orchestration or substantiate the skill’s delegation guidance. A routing campaign needs task families that exercise each row, held-out tasks per family, fixed acceptance tests, and enough independent generations to report uncertainty.

| Routing row | Needed task family and comparison evidence |
| --- | --- |
| Luna / low | Mechanical repository work: exact search, inventory, formatting, and constrained boilerplate; compare completion and verified-error rate against the other rows. |
| Luna / medium | Bounded code mapping and low-risk patches with explicit contracts and deterministic tests; measure correct scope discovery and passing changes. |
| Luna / high | Isolated implementation and bug-fixing tasks with deterministic acceptance suites; measure solution quality, latency, and rework. |
| Terra / medium | Broad exploration, large-file review, triage, and read-heavy synthesis tasks; score evidence coverage, diagnosis accuracy, and useful prioritization. |
| Sol / medium | Multi-agent coordination and integration tasks with independent workstreams; score decomposition, integration correctness, and coordinator overhead. |
| Sol / high | Ambiguous, cross-cutting concurrency, security, and migration tasks plus final review; score risk discovery, correctness, and regression avoidance. |

## Interpretation and threat model

- Preregister fixture version, matrix, trial count, seed, machine labels, exclusion rules, and primary metric before generation.
- Compare pass rates from independent generations. Use repeated hidden executions only to expose flaky artifacts.
- Preregister the minimum meaningful effect and power calculation. Analyze paired/block-aware contrasts by trial and machine; use medians and IQRs for latency and Holm-adjust the 15 pairwise comparisons. The built-in power approximation uses the more conservative Bonferroni alpha for planning.
- Keep the model mapping sealed until executable and policy scoring is final.
- The regex policy scan is a heuristic guard, not a semantic security analyzer. Manual or Roslyn-based review is required for strong policy claims.
- Behavior markers reject duplicates and contradictions but are not an authenticated channel. Because candidate code runs in the hidden-test process, a deliberately malicious candidate can forge console output; adversarial scoring requires an out-of-process runner with a protected result channel.
- The isolation protects against accidental test leakage and ordinary buggy candidates. A malicious assembly can still inspect loaded metadata. Strong adversarial secrecy requires a black-box, out-of-process evaluator in a separately trusted VM or service.
- Result files are structurally cross-checked against the plan and execution evidence but are not cryptographically signed. Preserve them in append-only storage or add external signatures when the benchmark operator is outside the trust boundary.
- The hidden-test source is public and versioned in this repository; “hidden” means it is withheld from a candidate workspace during evaluation, not adversarially secret. Do not make secrecy claims from this layout. A benchmark requiring unknown tests needs a separately operated private evaluator service.
- The generator uses a host bind mount without a disk quota and its restricted logs may still contain model-emitted sensitive text. Hostile generation requires a quota-backed volume or disposable VM plus automated redaction.
- Never modify a published fixture in place. Create a new version and regenerate its manifest.

## License

The benchmark is released under the [MIT License](LICENSE). The bundled `orchestrate` skill is an adaptation of Eric Provencher's MIT-licensed work and retains its upstream copyright notice in [`skills/orchestrate/LICENSE`](skills/orchestrate/LICENSE).
