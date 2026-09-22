# Model routing

## Routing defaults

Delegate when independent execution or model specialization justifies the handoff cost.
Starting with GPT-6, these defaults adapt [OpenAI's model-selection guidance](https://developers.openai.com/api/docs/guides/model-selection) to this skill's task classes. OpenAI describes Luna for scoped work and Sol for coding that needs more judgment; the exact class-to-effort mapping below is our **provisional hypothesis**, not an OpenAI-certified or locally benchmark-proven winner. Prefer verified correctness; compare cost, then time only when quality is tied on comparable work.

| Route | Use when | Default | Evidence status |
| --- | --- | --- | --- |
| `mechanical-repository-work` | Tightly bounded, low-risk mechanical work with cheap deterministic verification | `luna-low`: `gpt-6-luna`, `reasoning_effort: "low"` | hypothesis |
| `bounded-mapping-and-patch` | Bounded code mapping or a small low-risk patch with an explicit contract and strong checks | `luna-medium`: `gpt-6-luna`, `reasoning_effort: "medium"` | hypothesis |
| `isolated-implementation-debugging` | Isolated, well-specified implementation or repair with deterministic acceptance tests | `luna-medium`: `gpt-6-luna`, `reasoning_effort: "medium"` | hypothesis |
| `read-heavy-exploration-synthesis` | Structured defect localization with exact source-bound path, line, and excerpt evidence | `sol-medium`: `gpt-6-sol`, `reasoning_effort: "medium"` | hypothesis |
| `coordination-integration` | Multi-file contract, producer, consumer, and acceptance-state integration | `sol-medium`: `gpt-6-sol`, `reasoning_effort: "medium"` | hypothesis |
| `ambiguous-cross-cutting-high-risk` | Compatibility, implementation, rollback, and acceptance-state transitions under high risk | `sol-high`: `gpt-6-sol`, `reasoning_effort: "high"` | hypothesis |

Benchmark coordinator default: `gpt-6-sol` / `medium` (hypothesis). Preserve the user's current coordinator, including Astra; creating a worker does not change the parent model.

- Classify first. Break ties by safety rank, specificity, then precedence; uncertainty routes upward in risk.
- Availability fallback order: `luna-low` → `luna-medium` → `luna-high` → `sol-medium` → `sol-high`. Use the selected configuration; if unavailable, try only later entries. If none is available, keep the work with the coordinator.
- Check the task's acceptance criteria. A failed check is not an availability fallback: explicitly retry or reclassify at a stronger configuration and record the actual model, effort, outcome, time, and tokens when available. Do not describe this map as measured or optimal.
- Fast mode is session-level, not a `spawn_agent` parameter. Do not infer it from model or reasoning effort; report it enabled only when session state confirms it.

## Historical comparison archive

[Pre-GPT-6 comparison evidence](comparison-evidence.json) is retained for audit, but its scoped results do not override or validate the GPT-6 defaults above.
