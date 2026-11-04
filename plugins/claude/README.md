# Agent Workflow observation plugins (0.1.0)

These are passive local adapters, not a queue runner. Install the Python core from the
same release (`python3 -m pip install /path/to/agent-workflow`) with Python 3.11+.
A standalone marketplace archive does **not** contain the core or start the web server.
The hook's `python3` must import `agent_workflow`; activate the installed virtualenv before
starting Claude/Codex. OpenCode also accepts `AGENT_WORKFLOW_PYTHON=/absolute/venv/bin/python`.

Set `AGENT_WORKFLOW_HOME` to your chosen observer state directory **before starting the agent**.
Opt in each project and provider once:

```sh
agent-workflow --home /absolute/observer native register --provider codex --project /absolute/project
agent-workflow --home /absolute/observer native status --provider codex --project /absolute/project
```

Use `claude` or `opencode` for the other providers. Exact project paths are matched;
worktrees/subdirectories need their own registration. Already installed direct hooks
must first be removed with `native uninstall --provider TOOL --project PROJECT`.
Plugin registration and direct hook installation are alternative delivery modes.

## Install

Unzip the marketplace archive, or use the release repository checkout:

- Claude Code: `/plugin marketplace add /absolute/marketplace`, then
  `/plugin install agent-workflow-observer@agent-workflow`.
- Codex: `codex plugin marketplace add /absolute/marketplace`, then
  `codex plugin add agent-workflow-observer@agent-workflow`.
  Review the host's project trust and `/hooks` prompts yourself. Installation is not hook trust.
- OpenCode: run `npm install --prefix /absolute/project/.opencode --ignore-scripts /absolute/agent-workflow-observer-0.1.0.tgz`,
  then add `file:///absolute/project/.opencode/node_modules/agent-workflow-observer/index.js`
  to `opencode.json`'s `plugin` array, or copy `opencode/index.js` to
  `.opencode/plugins/agent-workflow-observer.js`. Preserve all existing config entries.
  Use only one of local/npm installs. This adapter targets the OpenCode 1.x plugin API.

Start a new agent session, then check `native status` for an observed timestamp.
Registration alone cannot prove that the host loaded/trusted a plugin.
Use an existing observer at `/sessions`, or explicitly start a separate observer:

```sh
agent-workflow --home /absolute/observer watch --port 8891
```

`watch` does not start a queue worker. Never substitute `serve`/`daemon` to inspect an
operational state home. Do not use both OpenCode HTTP polling and plugin observation for
the same source unless you want separate records (HTTP and plugin IDs are distinct).

## Remove / privacy / limits

`native unregister --provider TOOL --project PROJECT` (with the same `--home`) stops
recording, retains history, and does not remove any agent settings. Then uninstall the
plugin in the host (Codex: `codex plugin remove agent-workflow-observer@agent-workflow`;
Claude: `/plugin uninstall agent-workflow-observer@agent-workflow`; OpenCode: remove only
this plugin's entry/file). Other plugins and hooks are untouched.

Only session/project/provider IDs, event/state, tool/model names and parent identifiers
are stored. No transcripts, prompts, tool inputs, answers or approval decisions are read
from disk or persisted. Hook failure must not approve, reject or continue a model turn.
The core is a trusted local dependency, and hook subprocesses have a bounded timeout.

Missing events or 5 minutes without an event mean **unknown**, not idle or completion.
Claude/Codex Stop means waiting for input. OpenCode idle does not mean the process is alive.
OpenCode's bounded 64-event queue can drop events under sustained overload; transport
failure is intentionally silent in the agent, so check observer freshness and `native status`.
UI history is bounded (500 sessions / 10,000 events); it is not an exhaustive audit log.
OpenCode performs one startup GET of at most 100 recent project sessions, marking previously unseen sessions unknown; it cannot recover prior events. Claude/Codex do not discover old sessions retroactively. Plugins do not provide remote collection,
implement SSO/team permissions, or make WP completion claims.
