# ASTRA versus SOL: strategy before changing the default

Preregistered decision dated 2026-09-07: retain **SOL Medium as a provisional default** and
measure **ASTRA Low as a normal-use challenger**. Do not assume ASTRA belongs
only on exceptionally complex tasks, and do not promote it merely because it
is the most capable model. No real ASTRA/SOL campaign had been run at strategy freeze. The completed
[isolated v2 results](ASTRA_RESULTS.md) now provide measured worker evidence.

## Cost, speed and quality

Official Standard API prices per million tokens:

| Model | Input | Cached input | Output (including reasoning) |
| --- | ---: | ---: | ---: |
| SOL | $4 | $0.40 | $20 |
| ASTRA | $10 | $1 | $50 |

Sources: [SOL model](https://developers.openai.com/api/docs/models/gpt-5.6-sol),
[ASTRA model](https://developers.openai.com/api/docs/models/gpt-6-astra), and
[model guidance](https://developers.openai.com/api/docs/guides/latest-model),
retrieved 2026-09-07. Context7 also confirmed SOL pricing and reasoning support;
its retrieved snippets did not establish ASTRA performance. The official guide
reports lower task costs in some evaluations through fewer output tokens. That
is a reason to test ASTRA Low, not a measurement of this repository's workloads.
SOL's quoted pricing is promotional through at least 2026-11-21; recheck before
funding a campaign. Low and Medium are model-specific controls, not equal
compute budgets or latency promises.

At equal quality, tier and cache mix, ASTRA breaks even when its SOL-price-weighted
token volume falls to 40% of SOL's (60% reduction). With identical input I and
uncached Standard pricing, the exact condition is O_astra <= 0.4 O_sol - 0.12 I.
A 60% reduction in output alone is therefore insufficient when input remains
fixed. Fewer failed attempts can also improve cost per verified success.
Subscription credits are not API dollars. Long-context surcharges, cache writes,
tool charges and Fast pricing must be accounted for separately.

Optimize in this order: satisfy the quality floor, then compare total cost per
verified success and end-to-end time. Report the Pareto tradeoff when one model
is cheaper and the other faster; do not invent a universal weighted score.
Start with Standard service on both models. A Fast comparison is a separate
protocol revision with fresh support checks, especially for EU residency.

## Why change the verification experiment

The existing six-treatment sequential protocol uses an **ordinal hypothesis**
for cost and stops at the first passing stage. It cannot establish that ASTRA
Low is cheaper or faster than SOL Medium: the later treatment might never run.
The new `astra-comparison-v1.json` is a separate complete paired experiment:

- SOL Medium versus ASTRA Low, both on every planned fixture/replicate block.
- Six families × six fixtures × three independent generations × two treatments:
  **216 jobs**, 72 per physical host, with balanced AB/BA orders.
- Fresh contexts, identical prompts, frozen sealed evaluators, no worker delegation.
- Existing family quality floors; a tighter five-percentage-point noninferiority
  margin; a preregistered 15% generation-time gain hypothesis.
- Existing paired hierarchical bootstrap, multiplicity correction, machine,
  ecosystem and fixture robustness checks. An inconclusive result stays inconclusive.
- No sequential stopping, no retrospective winner selection, no policy promotion.

This is one-third the old 648-job complete envelope, although the old sequential
best case is only 108 jobs and answers a different question. A two-model study
cannot establish superiority over Luna/Terra. Six fixtures per family also give
limited precision and cannot substantiate broad concurrency/security claims.

Do not spend on a full ASTRA effort sweep initially. If the paired evidence
suggests complex-task failures or valuable gains, preregister a second study of
ASTRA Medium versus SOL High on untouched, appropriately difficult fixtures.
ASTRA High is a later escalation experiment only if Medium remains insufficient.
Never reuse a losing candidate failure as an infrastructure retry.

A worker study cannot choose the session-starting coordinator. Before changing
that default, repeat the separate live-coordinator experiment with ASTRA Low and
SOL Medium while keeping worker policy, prompts and concurrency fixed; then
confirm the selected end-to-end policy. This follow-up is not implemented by
relabelling worker integration scores.

## Run the paired study

Use v2 below. V1 was quarantined after account-linked integration startup was
observed; v2 disables those features and uses untouched reserve fixtures. See
[the execution record](ASTRA_RESULTS.md) before interpreting any results.

Runtime revision before execution (2026-09-07): the first live preflight with
CLI 0.147.0 did not advertise ASTRA. The generator is now pinned to 0.153.4,
with refreshed runtime/protocol hashes and new preflights. The preflight now
passes the selected runtime into the catalog query and normalizes an explicit
null `defaultServiceTier` as ordinary Standard service. No generated outcomes
were collected before this revision. Prior runtime-bound plans/readiness reports
must not be reused. The shared generator update also revises the legacy worker
and coordinator runtime pins; their treatment choices are unchanged.

All commands below use the existing isolated generation and sealed evaluation
pipeline. Validation and tests are generation-free. New authenticated preflights
on three physical hosts and a new protocol-bound readiness report are required.
The pinned CLI must actually advertise ASTRA and Standard service; absence stops
the study rather than silently substituting a model or changing the CLI pin.

```bash
python3 routing_campaign.py validate \
  --protocol protocols/astra-comparison-v2.json \
  --matrix protocols/astra-matrix-v1.json

python3 construct_readiness.py report \
  --protocol protocols/astra-comparison-v2.json \
  --docker-calibration runs/routing-docker-calibration-current.json \
  --output runs/astra-v2-readiness.json

# Execute independently on each physical host, changing machine ID and output.
python3 routing_preflight.py \
  --protocol protocols/astra-comparison-v2.json \
  --runtime-manifest protocols/astra-runtime-v2.json \
  --machine-id machine-a --auth-file /secure/benchmark-auth.json \
  --output runs/astra-v2-preflight-machine-a.json

python3 routing_campaign.py plan \
  --protocol protocols/astra-comparison-v2.json \
  --matrix protocols/astra-matrix-v1.json \
  --runtime-manifest protocols/astra-runtime-v2.json \
  --construct-readiness runs/astra-v2-readiness.json \
  --preflight runs/astra-v2-preflight-machine-a.json \
  --preflight runs/astra-v2-preflight-machine-b.json \
  --preflight runs/astra-v2-preflight-machine-c.json \
  --id-key-file /secure/astra-id.key --output runs/astra-v2-plan.json

# Repeat on each assigned host. Do not supply sequential state or manifest.
python3 routing_campaign_driver.py run-machine \
  --protocol protocols/astra-comparison-v2.json \
  --runtime-manifest protocols/astra-runtime-v2.json \
  --plan runs/astra-v2-plan.json --machine-id machine-a \
  --preflight runs/astra-v2-preflight-machine-a.json \
  --auth-file /secure/benchmark-auth.json --run-root runs/astra-v2

python3 astra_comparison.py runs/astra-v2/results/*.json \
  --plan runs/astra-v2-plan.json --output runs/astra-v2-report.json
```

The report recomputes the complete quality/latency analysis and includes a dated,
hash-bound **uncached Standard API cost proxy** per success, p50/p95 generation
latency and total seconds per success. All candidate failures cost tokens/time;
missing usage yields unknown cost, and zero successes yields no finite cost per
success. Reasoning tokens are not added to output a second time.

This proxy intentionally avoids claiming billing precision from aggregate CLI
telemetry. It ignores caching, cache writes, per-request long-context thresholds,
tools and infrastructure replacement attempts. Before making a monetary routing
claim, collect per-request billing/credit data and all attempt costs, preregister
an acceptable cost/latency tradeoff and paired cost uncertainty bounds, and publish
replayable evidence. `SUPPORTED` in the embedded legacy analysis means support
for quality plus generation latency, not cost savings. `policyPromotionAllowed`
is always false; the bundled routing policy remains provisional.

Practical interpretation: ASTRA Low becomes a candidate for normal use only
where quality is noninferior and measured task economics justify it. If gains
occur only in complex strata, use it there. If results are inconclusive, retain
SOL Medium and register a fixed larger confirmation using untouched fixtures;
do not keep sampling until a preferred answer appears.
