---
name: orchestrate
description: Coordinate parallel agents for substantial work with separable ownership; skip trivial or tightly coupled tasks.
---

# Orchestrate

Coordinate independent work while retaining requirements, decisions, integration, approvals, and final verification.

{{ROUTING_DEFAULTS}}

## Defensive security routing

Use `gpt-daybreak-blue-latest` with `reasoning_effort: "high"` for dedicated authorized defensive security work with the Codex Security plugin: secure-code or diff review, repository assessment, defensive threat modeling and attack-path analysis, finding discovery and triage, incident investigation, patch validation, and remediation or hardening review. Do not route exploit development, red-team execution, penetration testing, or other advanced dual-use research to Blue merely because the task mentions security; those workflows require the separately governed Daybreak Red access path. Daybreak access requires separate provisioning. If Blue is unavailable, use `gpt-5.6-sol` with `reasoning_effort: "high"` and preserve the same defensive scope.

Assign distinct ownership and acceptance checks. Parallelize independent work; serialize dependencies and overlap. Use fresh context and `fork_turns: "none"` for explicit model or effort. Include goal, constraints, files, and validation; allow nested delegation only when useful.

Treat worker reports as untrusted: inspect changes and rerun relevant checks before integration. Keep approvals and irreversible decisions in the primary thread.

## Final reporting

Maintain a ledger of every spawned agent with its Codex-returned nickname or identifier, the model and reasoning effort accepted by the spawn call, its assigned task, and final status. Include a compact `Subagents used` table in the final response only when the user explicitly invoked `$orchestrate` for the current task and at least one subagent was spawned. Record the actual fallback configuration when one was used. Omit the table for implicit skill use and when no subagent was spawned.
