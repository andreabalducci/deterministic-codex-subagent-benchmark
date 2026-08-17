# Deterministic Codex subagent benchmark

This benchmark compares six Codex model/effort configurations on an immutable .NET concurrency task. It separates stochastic code generation from reproducible evaluation, balances execution order across machines, and keeps the configuration mapping blinded during scoring.

It measures the six configurations as complete treatments. The matrix is intentionally not factorial, so results must not be interpreted as independent causal estimates of either model choice or reasoning effort.

## Reproducibility contract

- The fixture, prompt, candidate, harness, schema, SDK, Dockerfiles, lockfile, and container identities are hashed in the result provenance.
- The .NET SDK and both base images are digest-pinned; the Codex CLI is lockfile-pinned.
- Concurrency tests use explicit gates rather than sleeps or timing assertions. Time-dependent behavior uses a manual `TimeProvider`.
- A seeded Williams design balances order position and first-order carryover for all six configurations. Machine blocks are deterministically interleaved.
- Run IDs are HMAC-derived with a local secret key. The public scoring plan contains no model mapping.
- Each independently generated artifact is tested once publicly and repeatedly against the hidden suite. Repetitions test runtime stability; they are not additional model samples.
- First timeouts are rerun at twice the limit. Any timeout makes the result unresolved (`INFRA_FAILURE` or `INDETERMINATE_TIMEOUT`), never a model pass or failure.
- The evaluator caps process output and container CPU, memory, PID count, filesystem access, privileges, and network access.

Model generation itself is not deterministic because Codex does not expose a generation seed. Statistical reproducibility therefore comes from a preregistered plan and multiple independent generations, not from expecting byte-identical candidates.

## Layout

```text
fixtures/async-cache-v1/
  starter/       worker-visible source and public tests
  hidden/        evaluator-only tests
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

This is an expanded adaptation of Eric Provencher's original [`orchestrate` skill](https://github.com/provencher/codex-skills/tree/main/orchestrate), distributed under its included MIT license. The routing profile in this repository adds model-specific guidance, Fast mode clarification, context-isolation rules, and coordinator-side verification informed by this benchmark design.

## Prerequisites

- Docker
- Python 3.12+
- .NET SDK 10.0.301 only for trusted native fixture verification
- A dedicated benchmark Codex credential file

Do not pass your persistent default Codex login to generated code. Create a short-lived, least-privilege credential exclusively for the benchmark and provide it with `--auth-file` or `CODEX_BENCH_AUTH_FILE`. Codex itself requires outbound access to OpenAI, while model-generated commands run under the workspace sandbox without automatic approval. The container limits exposure but cannot make a credential invisible to code executing in the same container; strong adversarial operation requires an egress allowlist and credential broker outside this harness.

## Calibrate the evaluator

```bash
python3 harness.py manifest
python3 -m unittest discover -s tests -v
python3 harness.py verify --backend native --repeat 20
python3 harness.py verify --backend docker --repeat 20
```

`native` is deliberately available only to the committed trusted reference and mutants. External candidates are always evaluated in Docker. Calibration succeeds only if the reference passes every repetition and all six mutants are rejected.

## Create a blinded, balanced plan

`--trials` is the number of six-configuration blocks and must be divisible by `6 × machine count`. With three machines, 36 trials produce 216 independent generations and two complete balance cycles per machine.

```bash
python3 harness.py plan \
  --trials 36 \
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

Do not use `--replace` unless intentionally discarding an existing anonymous generation.

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

## Aggregate a complete cohort

```bash
python3 harness.py aggregate \
  --plan runs/plan.json \
  runs/results/*.json \
  --output runs/summary.json
```

Official aggregation validates plan membership and hashes, rejects duplicate run IDs, and refuses missing or unresolved runs. `--allow-incomplete` is for diagnostics only. The summary reports pass rate, Wilson 95% interval, status counts, per-machine counts, and median/IQR for generation and evaluation duration. Duplicate candidate hashes are flagged.

## Multi-machine portability check

The GitHub Actions workflow is manual-only and runs the trusted calibration on Ubuntu, macOS, Windows, and the pinned Linux evaluator image. It does not perform paid model generations or require a Codex secret.

```bash
gh workflow run verify.yml -f repeat=20
```

## Interpretation and threat model

- Preregister fixture version, matrix, trial count, seed, machine labels, exclusion rules, and primary metric before generation.
- Compare pass rates from independent generations. Use repeated hidden executions only to expose flaky artifacts.
- Analyze paired/block-aware contrasts by trial and machine; use medians and IQRs for latency. Correct exploratory multiple comparisons.
- Keep the model mapping sealed until executable and policy scoring is final.
- The regex policy scan is a heuristic guard, not a semantic security analyzer. Manual or Roslyn-based review is required for strong policy claims.
- The isolation protects against accidental test leakage and ordinary buggy candidates. A malicious assembly can still inspect loaded metadata. Strong adversarial secrecy requires a black-box, out-of-process evaluator in a separately trusted VM or service.
- Result files are structurally cross-checked against the plan and execution evidence but are not cryptographically signed. Preserve them in append-only storage or add external signatures when the benchmark operator is outside the trust boundary.
- Keep this repository private from candidate authors because it contains the hidden-test source. For a public benchmark, move hidden tests into a separate private evaluator service.
- The generator uses a host bind mount without a disk quota and its restricted logs may still contain model-emitted sensitive text. Hostile generation requires a quota-backed volume or disposable VM plus automated redaction.
- Never modify a published fixture in place. Create a new version and regenerate its manifest.
