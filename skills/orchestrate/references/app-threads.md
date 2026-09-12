# Asynchronous app threads

Use this mode for substantial independent work when separate task creation is authorized and `create_thread` and `send_message_to_thread` are available. A worker thread is a separate app task, not a `spawn_agent` child. The coordinator can remain on Astra while the worker uses the user's chosen model. Follow the current tool contracts for model overrides; if they require an explicit model choice and none was supplied, omit the override rather than treating a routing recommendation as authorization.

## Dispatch and yield

Resolve the originating thread's actual ID and host from session metadata or app tools. Never guess an ID. Give the worker a concise, self-contained brief: outcome, scope and ownership, relevant paths and decisions, acceptance criteria, and the originating thread ID (plus host when needed). Include artifact delivery instructions: a separate worktree's changes need its path and branch or commit for integration.

For project work, use `list_projects` and its returned project ID. Choose a worktree for a Git project unless the user requested the saved checkout directly; use local for a non-Git project. If the worker needs uncommitted changes or a particular branch, supply the required starting state only as authorized. Do not assume a new thread inherits conversation history or the current checkout's edits.

Ask the worker to send one completion message with `send_message_to_thread` to the origin, then finish its own turn. A blocker requiring coordinator input is also a reason to send a message. Ordinary progress needs no callback. A final response in the worker thread alone is not a callback.

Create the thread with the brief and supported model settings (`model` and `thinking` in the app tool; not `reasoning_effort` or `fork_turns`). Once creation is acknowledged, retain the returned identifiers, summarize the pending assignment, and end the coordinator turn. A pending `clientThreadId` is not a ready `threadId`; do not pass it to tools requiring a thread ID. Report setup as pending rather than claiming execution is complete. On a creation error, resolve it or continue locally before yielding.

Do not poll, call `wait_threads`, or schedule a heartbeat after successful dispatch in this mode. Event-based `wait_threads` remains an alternative when staying in the current turn is needed; passive waiting does not itself imply continuous model token generation.

## Worker brief example

> Complete [outcome] within [scope and ownership]. Relevant context: [paths, decisions, constraints]. Completion means [artifacts and acceptance checks]. Continue through relevant checks and repairs within scope. When done, call `send_message_to_thread` for origin [thread ID, host if needed] with your thread ID, completion status, artifact locations, verification results, and remaining limitations; then end your turn. If blocked and coordinator input is required, send the blocker and the specific decision needed instead. Do not send routine progress callbacks.

## Resume and accept

On a callback, associate it with the pending assignment and inspect its artifacts. Integrate and verify what the original request requires; address a blocker or send a focused follow-up to the existing worker rather than duplicating the task. Avoid reply loops: a completed worker needs no acknowledgement message that starts another turn. Ignore duplicate completion messages already handled.

If return messaging fails, the worker should leave its result and the delivery failure in its final response. Do not claim the coordinator was notified. A later user-requested status check can recover the result with `read_thread`; this workflow has no guaranteed automatic recovery from setup, runtime, or callback failure.

This is operational guidance, not a measured subscription saving. Existing benchmark evidence does not evaluate thread callbacks. Compare total coordinator and worker usage, handoff context, rework, and final quality before claiming an improvement.
