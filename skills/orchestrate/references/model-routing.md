# Model routing

## Measured comparison guidance

For work matching the tested scope below, use the observed cost option when cost matters; use the latency option when speed matters and deterministic acceptance checks are available. These scoped options take precedence over provisional defaults for matching work only. If a measured option is unavailable, return to the matching provisional route and its availability fallbacks; report the actual configuration used and retain its hypothesis status.

| Tested scope | Observed cost option | Supported latency option | Decision |
| --- | --- | --- | --- |
| Exact mechanical repository operations | `gpt-5.6-sol` / `medium` | `gpt-6-astra` / `low` | SUPPORTED |
| Bounded code mapping and low-risk patches | `gpt-5.6-sol` / `medium` | `gpt-6-astra` / `low` | SUPPORTED |
| Isolated implementation and deterministic debugging | Insufficient evidence | Insufficient evidence | INCONCLUSIVE |
| Structured defect localization with exact source evidence | Insufficient evidence | Insufficient evidence | CONTRADICTED |
| Multi-file contract, producer, consumer, and acceptance state integration | `gpt-5.6-sol` / `medium` | Insufficient evidence | INCONCLUSIVE |
| Compatibility, implementation, rollback, and acceptance state transitions | `gpt-5.6-sol` / `medium` | `gpt-6-astra` / `low` | SUPPORTED |

Pricing snapshot: 2026-09-07; uncached Standard API proxy per verified success, including failed attempts. Cost choices are descriptive among tested settings meeting the observed quality floor; they are not certified cheapest-sufficient routes or subscription-credit estimates.
Inconclusive or contradicted latency claims provide no latency recommendation. Insufficient evidence does not prove that an untested fallback is better. Exact source-record, JSON integration and rollback-state tests do not establish broad code-analysis, security, concurrency or live-coordinator capability.
Evidence: [comparison-evidence.json](comparison-evidence.json), content SHA-256 `59fa2b6ec9b62fab72e80397b12a434e596652bd5bc82962033821710a88fe4f`. Replay basis: hash-verified-prior-replay. The snapshot records local artifact replay commitments; rechecking private originals requires the collection command. It does not authorize cheapest-sufficient policy promotion.

## Routing defaults

Delegate when independent execution or model specialization justifies the handoff cost.

| Route | Use when | Default | Evidence status |
| --- | --- | --- | --- |
| `mechanical-repository-work` | Tightly bounded, low-risk mechanical work with cheap deterministic verification | `luna-low`: `gpt-5.6-luna`, `reasoning_effort: "low"` | hypothesis |
| `bounded-mapping-and-patch` | Bounded code mapping or a small low-risk patch with an explicit contract and strong checks | `luna-medium`: `gpt-5.6-luna`, `reasoning_effort: "medium"` | hypothesis |
| `isolated-implementation-debugging` | Isolated, well-specified implementation or repair with deterministic acceptance tests | `luna-high`: `gpt-5.6-luna`, `reasoning_effort: "high"` | hypothesis |
| `read-heavy-exploration-synthesis` | Structured defect localization with exact source-bound path, line, and excerpt evidence | `terra-medium`: `gpt-5.6-terra`, `reasoning_effort: "medium"` | hypothesis |
| `coordination-integration` | Multi-file contract, producer, consumer, and acceptance-state integration | `sol-medium`: `gpt-5.6-sol`, `reasoning_effort: "medium"` | hypothesis |
| `ambiguous-cross-cutting-high-risk` | Compatibility, implementation, rollback, and acceptance-state transitions under high risk | `sol-high`: `gpt-5.6-sol`, `reasoning_effort: "high"` | hypothesis |

Benchmark coordinator default: `gpt-5.6-sol` / `medium` (hypothesis). Preserve the user's current coordinator, including Astra; creating a worker does not change the parent model.

- Classify first. Break ties by safety rank, specificity, then precedence; uncertainty routes upward in risk.
- Cost order: `luna-low` → `luna-medium` → `luna-high` → `terra-medium` → `sol-medium` → `sol-high`. Use the selected configuration; if unavailable, try only later entries. If none is available, keep the work with the coordinator.
- Fast mode is session-level, not a `spawn_agent` parameter. Do not infer it from model or reasoning effort; report it enabled only when session state confirms it.
