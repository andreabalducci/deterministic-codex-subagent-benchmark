# ASTRA Low versus SOL Medium — measured results

**Decision, 2026-09-07:** retain SOL Medium as the provisional cost-oriented
default. Offer ASTRA Low for latency-sensitive routine tasks with deterministic
verification. ASTRA is not restricted to difficult work: it was faster even on
mechanical edits and bounded patches. It is also not a cheaper blanket replacement
for SOL Medium under the dated API-price scenarios measured here.

The isolated v2 campaign completed **216 independent generations**, 108 per
configuration, on three physical hosts. All 216 records passed local schema,
plan, source-hash, transcript, usage, attempt and candidate replay checks. The
worker protocol used Standard service, fresh contexts, a frozen CLI 0.153.4,
explicitly disabled account integrations, and the previously untouched fixtures
07–12 in each family. Three unscored ASTRA isolation smokes each had exactly
11,878 input tokens and no integration startup errors.

## Observed tradeoff

| Metric | SOL Medium | ASTRA Low |
| --- | ---: | ---: |
| Strict end-to-end successes | 88 / 108 | 86 / 108 |
| Input tokens, including cached reads | 9,157,131 | 6,793,531 |
| Output tokens | 182,866 | 83,517 |
| Total tokens | 9,339,997 | 6,877,048 |
| Mean generation time | 72.99 s | 42.40 s |
| Median generation time | 68.08 s | 34.12 s |
| Total measured time per success | 91.45 s | 55.23 s |
| Uncached Standard API cost proxy, all 108 attempts | $40.29 | $72.11 |
| Uncached proxy per success | $0.458 | $0.839 |
| Cache-discount scenario, all 108 attempts | $12.75 | $20.65 |
| Cache-discount scenario per success | $0.145 | $0.240 |

ASTRA used **26.4% fewer total tokens** and **54.3% fewer output tokens**. Its
mean generation time was **41.9% lower**, and its median was about half of SOL's.
But its uncached cost proxy per success was **83.2% higher**; the cache-discount
scenario was **65.8% higher**. These are descriptive ratios, not preregistered
cost confidence bounds. Quality differences are too small, and the sample too
limited, to claim overall quality superiority for either model.

Cost uses the 2026-09-07 Standard API rates in the frozen pricing file: SOL
$4/$0.40/$20 and ASTRA $10/$1/$50 per million input/cached-input/output tokens.
[Official SOL pricing](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
and [official ASTRA pricing](https://developers.openai.com/api/docs/models/gpt-6-astra)
were checked before execution. Reasoning is not added to output a second time;
the CLI did not expose separate reasoning counts reliably.

The uncached proxy prices all input at the uncached rate. The additional
cache-discount scenario is `(input - cached) * input_rate + cached * cache_rate
+ output * output_rate`. Neither is an invoice or a measure of subscription
credits. Cache-write billing, per-request long-context effects, tool fees and
infrastructure setup are not reconstructed. All scored failures remain in costs.
The discarded first campaign and three isolation smokes are excluded from these
comparative totals, not treated as free operations.

## Task-family results

Each cell represents 18 independent generations. The preregistered statistical
claim combines an absolute quality floor, noninferiority, a minimum 15%
generation-time gain, and robustness checks. **SUPPORTED does not mean cheaper
or authorize routing-policy promotion.**

| Family | SOL successes | ASTRA successes | ASTRA quality + latency claim |
| --- | ---: | ---: | --- |
| Mechanical repository operations | 18/18 | 18/18 | SUPPORTED |
| Bounded mapping and patch | 18/18 | 18/18 | SUPPORTED |
| Isolated implementation/debugging | 15/18 | 15/18 | INCONCLUSIVE |
| Exact source-evidence localization | 1/18 | 0/18 | CONTRADICTED |
| Multi-file JSON contract integration | 18/18 | 17/18 | INCONCLUSIVE |
| High-risk compatibility/rollback JSON states | 18/18 | 18/18 | SUPPORTED |

For mechanical, bounded-patch and high-risk JSON-state tasks, the simultaneous
lower bounds on ASTRA's generation-time savings exceed 33%. For integration,
the confidence interval crosses no gain because of a long failing generation.
For implementation, both models are below the 85% absolute floor.

One ASTRA integration generation exited unsuccessfully after a connection-reset
error; its preserved artifact passes replay. It remains a failure under the
frozen end-to-end rule and was not retried or retrospectively rescored. Thus the
86/108 count is not a pure count of incorrect ASTRA artifacts. Pure artifact
acceptance would be 87/108, a descriptive sensitivity result only.

The source-analysis task is an exact-reference-record test. Almost universal
failure is not evidence that both models are generally unable to analyze code.
For example, `read-heavy-08` expects two Compose records, while an inspected
answer selected the server's listen statement. The prompt's executable-statement
wording and the completeness/relevance of the frozen gold records warrant a
construct review before reusing this family for a general routing decision.
No gold answers were changed during this campaign.

High-risk fixtures check exact JSON state transitions, not real security audits,
concurrency changes or migrations. Worker integration is not live coordination.
No result here establishes an ASTRA coordinator default or ASTRA Medium/High
performance.

## Operational recommendation

- **Cost first:** keep SOL Medium as the working default. Its hypothesis status
  remains unchanged; the existing Luna/Terra routes were not tested here.
- **Latency first:** ASTRA Low is a useful routine-work option where checks are
  deterministic. It need not be reserved for exceptional complexity.
- **Complex implementation:** neither tested setting met the quality floor.
  Preregister an ASTRA Medium versus SOL High study on new fixtures before
  selecting an escalation route. Do not infer that raising effort fixes failures.
- **Source analysis:** review the instrument before spending on another model
  comparison using that task family.

No arbitrary cost-versus-time weight is imposed. The data show a Pareto tradeoff:
ASTRA is faster and more token-efficient; SOL costs less in both API scenarios.
The cheapest-sufficient policy remains provisional. The bundled skill now derives
its scoped comparison guidance automatically from collected evidence; no model
name is hard-coded as the winning choice in that derivation.

## Execution and evidence

The original v1 campaign was stopped after account-linked MCP/plugin startup was
observed despite `--ignore-user-config`. Its partial outcomes remain quarantined
and are not pooled into v2. Two missing-evaluator-image startup incidents in v1
were recovered by scoring preserved candidates without regenerating them. The
preflight now provisions both images before generation. No unresolved
infrastructure failure remains in the completed v2 cohort.

Private plans, candidates, credentials and transcripts are ignored by Git. The
task-created credential copies and transport archives were removed after the
campaign; original account logins were not modified. Full local v2 evidence is
under `runs/astra-v2/`; it is not a published, policy-promoting evidence bundle.

Reviewable aggregate artifacts:

- [Summary and token/cost scenarios](../reports/astra-comparison-2026-09-07-summary.json)
- [Preregistered family analysis and cost proxies](../reports/astra-comparison-2026-09-07-analysis.json)
- [Local replay-audit summary](../reports/astra-comparison-2026-09-07-audit.json)
- [Executed v2 protocol](../protocols/astra-comparison-v2.json)
- [Executed runtime exclusions](../protocols/astra-runtime-v2.json)

Recompute from the preserved local cohort:

```bash
python3 astra_comparison.py runs/astra-v2/results/*.json \
  --protocol protocols/astra-comparison-v2.json --plan runs/astra-v2-plan.json \
  --output runs/astra-v2/comparison-report-recomputed.json
```

## Automated collection

`routing_comparison_evidence.py` now collects the cohort, recomputes the analysis,
checks artifact hashes and generates the skill in one command. See the
[collection workflow](../README.md#automated-comparison-evidence-and-skill-generation).
The current snapshot reused the existing local replay audit after checking all
216 records and preserved candidates, because Docker was unavailable on the
collection host. A fresh replay remains the default mode when Docker is running.
