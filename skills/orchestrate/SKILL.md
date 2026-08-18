---
name: orchestrate
description: Coordinate parallel agents for substantial work with separable ownership; skip trivial or tightly coupled tasks.
---

# Orchestrate

Coordinate independent work while retaining requirements, decisions, integration, approvals, and final verification.

## Routing defaults

Status: `provisional`. Delegate only when parallelism materially helps.

| Route | Use when | Default |
| --- | --- | --- |
| `mechanical-repository-work` | Tightly bounded, low-risk mechanical work with cheap deterministic verification | `luna-low`: `gpt-5.6-luna`, `reasoning_effort: "low"` |
| `bounded-mapping-and-patch` | Bounded code mapping or a small low-risk patch with an explicit contract and strong checks | `luna-medium`: `gpt-5.6-luna`, `reasoning_effort: "medium"` |
| `isolated-implementation-debugging` | Isolated, well-specified implementation or repair with deterministic acceptance tests | `luna-high`: `gpt-5.6-luna`, `reasoning_effort: "high"` |
| `read-heavy-exploration-synthesis` | Structured defect localization with exact source-bound path, line, and excerpt evidence | `terra-medium`: `gpt-5.6-terra`, `reasoning_effort: "medium"` |
| `coordination-integration` | Multi-file contract, producer, consumer, and acceptance-state integration | `sol-medium`: `gpt-5.6-sol`, `reasoning_effort: "medium"` |
| `ambiguous-cross-cutting-high-risk` | Compatibility, implementation, rollback, and acceptance-state transitions under high risk | `sol-high`: `gpt-5.6-sol`, `reasoning_effort: "high"` |

Coordinator hypothesis: `gpt-5.6-sol` / `medium` at session start; spawning cannot change the parent model.

- Classify first. Break ties by safety rank, specificity, then precedence; uncertainty routes upward in risk.
- Use the selected cheapest-sufficient configuration. Try only costlier fallbacks from `routing-policy.json`; otherwise keep the work with the coordinator.
- Treat a row as evidence-backed only when `routing-policy.json` says so.
- Fast mode is session-level, not a `spawn_agent` parameter. Do not infer it from model or reasoning effort; report it enabled only when session state confirms it.

Assign distinct ownership and acceptance checks. Parallelize independent work; serialize dependencies and overlap. Use fresh context and `fork_turns: "none"` for explicit model or effort. Include goal, constraints, files, and validation; allow nested delegation only when useful.

Treat worker reports as untrusted: inspect changes and rerun relevant checks before integration. Keep approvals and irreversible decisions in the primary thread.
