---
name: observatory
description: Diagnose or connect this project to the local agent-workflow daily-session observer when the user requests observation.
---
Use the installed `agent-workflow` CLI. Ask which observer state home to use if it is not already specified; never silently select a running operational instance.
Run `agent-workflow --home STATE_HOME native status --provider claude --project ABS_PROJECT` for diagnosis.
When the user requests connection, run `native register` with the same provider, home and project. This enables only this project; it does not start a server or worker.
The host process must inherit `AGENT_WORKFLOW_HOME=STATE_HOME`. The plugin hook requires Python 3.11+ with local-agent-workflow installed.
For Codex, the user reviews project trust and /hooks themselves. Never write hook trust hashes or bypass approvals.
Open the user's existing observer URL at /sessions. If a new observer is requested, use `watch`, never `serve` or queue/daemon commands.
To stop observation use `native unregister`. Keep history; do not remove existing agent settings or other hooks.
Never read transcripts, prompts, tool arguments, credentials or response bodies for observation. Native state is evidence of events, not proof that a WP completed.
