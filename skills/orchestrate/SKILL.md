---
name: orchestrate
description: Delegate substantial, separable work to agents or app threads and integrate their results.
---

# Orchestrate

Retain requirements, decisions, integration, and final acceptance in the coordinator. Handle small or tightly coupled work directly when handoff costs outweigh the benefit.

## Choose execution mode

- For an authorized separate app task that can run independently, read [app-threads.md](references/app-threads.md). Dispatch, end the coordinator turn, and resume on the worker's completion or blocker message.
- For parallel work within the current turn, use available subagent tools. Assign distinct ownership, serialize dependencies and overlapping edits, and supply only the context each worker needs. With `spawn_agent`, explicit model or effort requires `fork_turns: "none"` or a bounded history fork; a full-history fork inherits the parent settings.
- If delegation or return messaging is unavailable, keep dependent work in this thread. Do not promise an automatic callback without the required tools.

When choosing a worker model, read [model-routing.md](references/model-routing.md). Respect explicit user choices; measured recommendations apply only to their tested scope. Other defaults remain hypotheses.

For dedicated authorized defensive security work, read [defensive-security.md](references/defensive-security.md).

## Completion

Give workers an outcome, relevant files and constraints, acceptance checks, and a stopping condition. Let them choose implementation steps. Continue through implementation, relevant verification, and repairs within the authorized scope; a first draft is not completion when the request requires a working result.

Inspect worker artifacts and verification evidence before integration. Run additional checks when changes, failures, or missing evidence justify them; do not repeat successful checks solely because a worker performed them. Keep irreversible actions within the user's authorization.

## Final reporting

Track each worker's returned identifier, actual model and effort when confirmed, assignment, and status. Record requested settings as unconfirmed when the runtime does not attest them, and record any fallback. Include a compact `Subagents used` table only when the user explicitly invoked `$orchestrate` for the current task and at least one worker was created; identify app threads as such. Omit the table for implicit use or when no worker was created.
