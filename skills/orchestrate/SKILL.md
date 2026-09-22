---
name: orchestrate
description: Delegate substantial, separable work to agents or app threads and integrate their results.
---

# Orchestrate

Retain requirements, decisions, integration, and final acceptance in the coordinator. Handle small or tightly coupled work directly when handoff costs outweigh the benefit.

## Choose execution mode

- When the user explicitly requests a new separate app task that can run independently, read [app-threads.md](references/app-threads.md). Invoking this skill or requesting subagents alone does not authorize `create_thread`. Dispatch, end the coordinator turn, and resume on the worker's completion or blocker message.
- For parallel work within the current turn, use available subagent tools. Assign distinct ownership, serialize dependencies and overlapping edits, and supply only the context each worker needs. With `spawn_agent`, explicit model or effort requires `fork_turns: "none"` or a bounded history fork; a full-history fork inherits the parent settings.
- If delegation or return messaging is unavailable, keep dependent work in this thread. Do not promise an automatic callback without the required tools.

When choosing a worker model, read [model-routing.md](references/model-routing.md). Respect explicit user choices. Starting with GPT-6, its defaults adapt OpenAI guidance as hypotheses; historical comparisons do not establish GPT-6 winners.

For dedicated authorized defensive security work, read [defensive-security.md](references/defensive-security.md).

## Subagent lifecycle

Follow the tools exposed in the current session; do not assume legacy lifecycle tools are available. Retain each identifier returned by `spawn_agent` and use it or the returned task path when addressing that worker. Respect the session's concurrency limit, including the coordinator, and continue independent local work while workers run.

- Use `send_message` to supply context or steering. It does not start a new turn for an idle worker.
- Use `followup_task` for additional work: it starts a turn for an idle worker and delivers steering to a running worker. Reuse the existing worker when its context is relevant.
- Use `interrupt_agent` to stop the current turn. The worker remains available for messages and follow-up work; interruption is not completion.
- Use `list_agents` to inspect the team and `wait_agent` when waiting for worker activity. A wait reports mailbox activity or timeout, not the worker's result; inspect the separately delivered messages and final-status notifications before accepting completion. Avoid repeated status polling.

## Completion

Give workers an outcome, relevant files and constraints, acceptance checks, and a stopping condition. Let them choose implementation steps. Continue through implementation, relevant verification, and repairs within the authorized scope; a first draft is not completion when the request requires a working result.

Inspect worker artifacts and verification evidence before integration. Run additional checks when changes, failures, or missing evidence justify them; do not repeat successful checks solely because a worker performed them. Keep irreversible actions within the user's authorization.

## Final reporting

Track each worker's returned identifier, actual model and effort when confirmed, assignment, and status. Record requested settings as unconfirmed when the runtime does not attest them, and record any fallback. Include a compact `Subagents used` table only when the user explicitly invoked `$orchestrate` for the current task and at least one worker was created; identify app threads as such. Omit the table for implicit use or when no worker was created.
