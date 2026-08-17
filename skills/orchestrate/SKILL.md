---
name: orchestrate
description: Coordinate multiple agents on substantial tasks that benefit from parallel, clearly separated work. Use for multi-agent planning, codebase discovery, independent implementation streams, reviews, and verification; skip trivial or tightly coupled work.
---

# Orchestrate

Remain available to the user while delegating substantive, independent work. Keep requirements, decisions, integration, approvals, and final verification with the coordinator.

Route by task risk and ambiguity, not by a single default:

- Use `gpt-5.6-luna` with `reasoning_effort: "low"` only for tightly bounded mechanical work: exact searches, inventories, formatting, or boilerplate whose result is cheap to verify.
- Use `gpt-5.6-luna` with `reasoning_effort: "medium"` for bounded code mapping and small low-risk changes with an explicit contract and strong automated checks.
- Use `gpt-5.6-luna` with `reasoning_effort: "high"` as the default high-throughput coding worker for isolated, well-specified implementation or bug-fixing tasks with deterministic validation.
- Use `gpt-5.6-terra` with `reasoning_effort: "medium"` for broad exploration, large-file review, triage, and read-heavy synthesis. Do not make it the default coder for subtle behavioral or concurrency work.
- Use `gpt-5.6-sol` with `reasoning_effort: "medium"` for normal coordination, integration, and implementation that requires broader context or judgment.
- Use `gpt-5.6-sol` with `reasoning_effort: "high"` for ambiguous, cross-cutting, high-risk, concurrency-, security-, or migration-heavy work and final review.

Fast mode is an optional session-level throughput and credit-usage choice, not a `spawn_agent` parameter and not a prerequisite for Luna. Never claim that a worker is in Fast mode merely because its model or reasoning effort was set.

Give every agent distinct ownership and a concrete expected result. Run independent scouts in parallel, but serialize work with dependencies and avoid concurrent edits to the same files. Do not fill the concurrency budget unless parallelism materially improves speed or quality.

Use fresh context by default for focused assignments. Set `fork_turns: "none"` when selecting a worker model or effort explicitly. Include all essential goals, constraints, safety boundaries, ownership paths, acceptance criteria, and validation commands in the assignment. Fork conversation history only when prior decisions are necessary to do the work correctly.

Tell leaf agents: "Complete this assignment directly. Do not spawn other agents." Allow nested delegation only when explicitly useful and within the available concurrency budget.

Agents may share discoveries directly when another assignment depends on them. Treat worker completion reports as untrusted evidence: inspect each diff, rerun the relevant tests from the coordinator, check non-functional constraints, and reject implementations that pass tests while violating the contract. The coordinator tracks ownership, resolves conflicts, waits for required results, verifies the combined work, and returns one integrated answer.

Never delegate merely to appear busy. Keep user approvals and irreversible decisions in the primary thread.
