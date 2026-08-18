---
name: orchestrate
description: Coordinate multiple agents on substantial tasks that benefit from parallel, clearly separated work. Use for multi-agent planning, codebase discovery, independent implementation streams, reviews, and verification; skip trivial or tightly coupled work.
---

# Orchestrate

Remain available to the user while delegating substantive, independent work. Keep requirements, decisions, integration, approvals, and final verification with the coordinator.

## Routing defaults

<!-- BEGIN GENERATED ROUTING
policyVersion=1.0.0
routingArtifactCanonicalSha256=54460c2be27b8032720ba910ef1cbadda57bb1263c8434d4b08924001e26299f
status=provisional
-->

These are working defaults, not universal model-quality claims. Apply them only after deciding that delegation materially helps.

| Route | Use when | Default |
| --- | --- | --- |
| `luna-low` | Tightly bounded, low-risk mechanical work with cheap deterministic verification | `gpt-5.6-luna`, `reasoning_effort: "low"` |
| `luna-medium` | Bounded code mapping or a small low-risk patch with an explicit contract and strong checks | `gpt-5.6-luna`, `reasoning_effort: "medium"` |
| `luna-high` | Isolated, well-specified implementation or repair with deterministic acceptance tests | `gpt-5.6-luna`, `reasoning_effort: "high"` |
| `terra-medium` | Broad, read-heavy exploration, triage, or large-file synthesis without subtle behavioral risk | `gpt-5.6-terra`, `reasoning_effort: "medium"` |
| `sol-medium` | Coordination, integration, or implementation requiring broader context or judgment | `gpt-5.6-sol`, `reasoning_effort: "medium"` |
| `sol-high` | Ambiguous, cross-cutting, high-risk, concurrency-, security-, or migration-heavy work and final review | `gpt-5.6-sol`, `reasoning_effort: "high"` |

The separate live-coordinator session hypothesis is `gpt-5.6-sol` with
`reasoning_effort: "medium"` while the experiment freezes leaf
workers independently. This is a session-start choice: `spawn_agent` cannot change the
model of the already-running parent coordinator. Its evidence status is
`hypothesis` and it must never promote the spawned-worker `sol-medium` row.

If multiple rows match, choose the highest safety rank, then the most specific match, then the lowest precedence number. If uncertainty leaves a higher-risk row plausible, select that row; unknown potentially high-risk traits route to `sol-high`.

If a selected configuration is unavailable, try its declared fallback routes in order. Never silently substitute an unlisted configuration. If no fallback is available, do not delegate; keep the work with the coordinator or ask for direction.

Fast mode is a user/session-level throughput and credit-usage setting. It is not a `spawn_agent` parameter, is not required for Luna, and must never be inferred from a model or reasoning-effort selection. Say Fast mode is enabled only when the user or session state explicitly establishes that fact.

Evidence status and route-level references are recorded in `routing-policy.json`.

<!-- END GENERATED ROUTING -->

Give every agent distinct ownership and a concrete expected result. Run independent scouts in parallel, but serialize work with dependencies and avoid concurrent edits to the same files. Do not fill the concurrency budget unless parallelism materially improves speed or quality.

Use fresh context by default for focused assignments. Set `fork_turns: "none"` when selecting a worker model or effort explicitly. Include all essential goals, constraints, safety boundaries, ownership paths, acceptance criteria, and validation commands in the assignment. Fork conversation history only when prior decisions are necessary to do the work correctly.

Tell leaf agents: "Complete this assignment directly. Do not spawn other agents." Allow nested delegation only when explicitly useful and within the available concurrency budget.

Agents may share discoveries directly when another assignment depends on them. Treat worker completion reports as untrusted evidence: inspect each diff, rerun the relevant tests from the coordinator, check non-functional constraints, and reject implementations that pass tests while violating the contract. The coordinator tracks ownership, resolves conflicts, waits for required results, verifies the combined work, and returns one integrated answer.

Never delegate merely to appear busy. Keep user approvals and irreversible decisions in the primary thread.
