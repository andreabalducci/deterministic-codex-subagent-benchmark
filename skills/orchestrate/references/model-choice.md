# Models and reasoning

Official guidance checked on 2026-09-29. Recheck the linked pages when model availability or supported controls change. User-selected settings take precedence; creating a worker does not change the coordinator.

## Model roles

The [GPT-6.1 Sol announcement](https://openai.com/it-IT/index/introducing-gpt-6-1-sol/) reports improvements over GPT-6 Sol in coding, computer use, professional work, and science. It places Sol 6.1 near Astra on several evaluations while retaining Astra as the strongest model overall. Its standard API input/output rates are one fifth of Astra's; this is not a guarantee about cost per completed task or Codex credit usage.

Adapt the [official model-selection guide](https://developers.openai.com/api/docs/guides/model-selection) as follows:

| Model | Work to assign | Starting effort |
| --- | --- | --- |
| `gpt-6-luna` | Clear, repeatable extraction, transformations, triage, or focused coding with explicit checks | Codex generally recommends `high`; consider `low` for mechanical edits and `medium` for bounded patches |
| `gpt-6.1-sol` | Default for complex coding, sustained debugging, exploration, integration, and recurring workflows, including much work previously assigned to Astra | Client default; `medium` when an explicit setting is required |
| `gpt-6-astra` | Reserve for the hardest investigations or scientific problems, or when a task-specific comparison demonstrates enough added quality to justify its cost | `low` as the Codex starting point; `medium` for broad projects, `xhigh` for demanding analysis |

These assignments are local adaptations, not measured winners. Keep the six benchmark routes in [model-routing.md](model-routing.md) provisional. Its lower Luna settings assume tightly bounded work and cheap deterministic verification. Broader or uncertain work needs reclassification. Astra and efforts beyond `high` are available judgment choices outside that benchmark matrix; do not claim the resolver selected them.

## Reasoning and escalation

The [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning) distinguishes effort from model choice:

- `low`: execution-focused work with modest planning and tool use.
- `medium`: a balanced baseline for agentic coding, research, coordination, and tasks requiring judgment.
- `high`: difficult debugging, deeper planning, and complex multi-step work. Compare it with `medium` on representative tasks.
- `xhigh`: long investigations and difficult review when added depth justifies latency and usage. The selection guide also suggests Sol at this effort for polished deliverables or conflicting evidence.
- `max`: hardest single problems when additional reasoning shows a useful benefit. It is not the routine default.

Use Sol 6.1 as the first choice for most complex work when cost matters; complexity alone does not require Astra. For a failed acceptance check, diagnose the cause before retrying. Repair missing context or a broken tool directly. If the task needs more reasoning, compare Sol at `high` or `xhigh` before paying for Astra. Reserve Astra for a demonstrated Sol capability limit, the hardest problems, or an explicit preference for maximum quality over cost. This is a local escalation heuristic. Do not cycle automatically through every level or assume Luna at higher effort equals Sol or Astra.

Compare identical inputs and acceptance criteria. Track correctness, retries, latency, tokens, and cost per successful task where available. Retain the lightest configuration that meets the quality bar. Older benchmark evidence does not validate Sol 6.1 or establish equivalent effort across generations.

## Product controls and compatibility

[Codex and Work model documentation](https://learn.chatgpt.com/docs/models) recommends the client default for Sol 6.1, High for Luna, and Light for Astra. Light maps to `low`; Extra High maps to `xhigh`. Available controls depend on the client, plan, and workspace. Verify the settings exposed by the actual tool before dispatching.

Max gives a model more time on a single task. Ultra uses subagents for separable work; it is not simply a higher serial reasoning budget. Luna supports Max, not Ultra. Choose Ultra only when parallel parts justify coordination and delegation is authorized. Do not add hidden parallelism to an already delegated worker by default.

The [Sol 6.1 API model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol) supports `low`, `medium` (default), `high`, `xhigh`, and `max`, but not `none` or `minimal`. API tool calling requires Responses. Do not send Codex's `ultra` label as API `reasoning.effort`, infer API support from a generic tool enum, or confuse API `reasoning.mode: "pro"` with effort or a subscription plan.

Use the exact new identifier `gpt-6.1-sol` for new Sol selections. The [migration guide](https://developers.openai.com/api/docs/guides/latest-model) retains supported effective effort when migrating; use `low` instead of an unsupported `none` or `minimal`. Preserve an explicit request for GPT-6 Sol; its continued availability does not mean it is still the preferred Sol default.

Speed is separate from reasoning. Fast and Ultrafast require confirmed client or API support and access. Do not infer their availability from the announcement or include unsupported speed parameters in `spawn_agent`; the launch announcement and client documentation differ on Sol 6.1 Ultrafast rollout timing.
