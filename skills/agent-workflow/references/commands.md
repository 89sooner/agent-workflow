# CLI reference for agents

Example task creation (replace project/WP/criteria/checks with actual task context):

```bash
agent-workflow create WP-074 --project /path/to/project \
  --goal '사용자가 확인할 결과' \
  --criterion '필수 동작과 검증 범위' \
  --ref docs/40_delivery/work_packages.md \
  --check 'python3 -m unittest discover -s tests' \
  --owner claude --reviewer codex
```

`--check` and `--criterion` can repeat. Checks run as argv with no shell; use a
repository script for pipelines or environment setup. Check commands execute
with the caller's privileges, so only register commands within the user's scope.
There must be at least one meaningful passing check to finish. A task can be
registered before its checks are known and receive them using `check-add`.

```bash
agent-workflow show WP-074
agent-workflow brief WP-074
agent-workflow checkpoint WP-074 --summary '실제 변경과 확인 결과' --next '다음 행동'
agent-workflow check WP-074 --timeout 300
agent-workflow brief WP-074 --role review
agent-workflow review-import WP-074 --file /tmp/review.json --reviewer codex --fingerprint SHA256
agent-workflow finish WP-074
agent-workflow handoff WP-074
```

Review JSON:

```json
{
  "outcome": "pass",
  "summary": "독립 검토자가 실제로 확인한 범위",
  "next_action": "등록된 검사 결과 확인 후 마감",
  "findings": []
}
```

Use `changes_requested` with blocking findings or `blocked` when the review is
incomplete. Never import this example as actual review evidence.

```bash
agent-workflow run WP-074 --dry-run
agent-workflow run WP-074 --timeout 900 --repairs 1
agent-workflow assign WP-074 --owner codex --reviewer claude
```

`run` is a foreground, finite process. `daemon start` plus `queue ID` runs it in
the background and exposes the local dashboard. A WSL shutdown ends the daemon;
start it again to process retained queued work. Interrupted jobs are not retried.
Both agents need their own working login and quota. Each process has a timeout;
task execution settings add invocation and cumulative-command-time limits. These
are not a guaranteed provider billing cap. Task refs are file paths, not whole files
automatically uploaded in the prompt; the selected agent may read them as needed.

State: `${AGENT_WORKFLOW_HOME:-~/.local/state/agent-workflow}`. Keep it outside
the target project. Logs can contain source code or command output. They are
local private files; do not publish them without reviewing their contents.

Full export is not performed. Semantic resume uses the latest checkpoint and
current source, not a native Codex/Claude session ID. Existing native sessions
are not silently resumed or killed.

## Release and queue

After the user has selected the actual target:

```bash
agent-workflow release-config WP-074 --repo OWNER/REPO --remote origin \
  --base main --branch workflow/wp-074 --path src --path tests \
  --deploy 'python3 scripts/deploy.py' --health 'python3 scripts/health.py'
agent-workflow pipeline WP-074 --dry-run
agent-workflow daemon start --port 8776
agent-workflow queue WP-074
agent-workflow jobs
agent-workflow daemon status
```

Release commands are argv, never implicit shell strings. The scripts execute from
the exact merged checkout with WORKFLOW_REVISION and WORKFLOW_TASK_ID. Deployment
is not proven by a command's text report: health must confirm the expected version.
Without release-config, a pipeline stops after local completion. Use `release ID`
to continue a configured release without rerunning implementation. Set --allow-no-ci
only for a repository intentionally lacking CI; normal CI failure blocks merge.
`--mode local` supports a local bare Git remote for test releases, not a GitHub PR.
Git identity must already be configured in the project for automatic commit/merge.

## Project profiles and v0.3 operations

See [the current usage guide](../../../docs/AUTOMATION_V0.3.ko.md) for the JSON schema,
managed document blocks, service/browser fixtures, export boundaries, and model policies.

```bash
agent-workflow profile save PROJECT --file workflow.profile.json
agent-workflow create TASK_ID --profile PROJECT --goal '실제 목표' --criterion '실제 완료 조건'
agent-workflow prepare TASK_ID
agent-workflow run TASK_ID --recoveries 1
agent-workflow execution-config TASK_ID --file execution.json
agent-workflow stop JOB_ID
agent-workflow resume JOB_ID
agent-workflow finalize TASK_ID
agent-workflow cleanup TASK_ID
agent-workflow notifications
agent-workflow service-install --port 8776
```

`service-install` only prepares files without `--enable`. With enable, systemd is
used when available; otherwise a managed Bash login block starts the daemon.
This does not configure Windows login or WSL boot itself.

```bash
agent-workflow depends TASK_B --on TASK_A --require merged
agent-workflow batch create APPROVED_SET --task TASK_A --task TASK_B --max-jobs 2 --max-seconds 3600
agent-workflow batch start APPROVED_SET
agent-workflow batch show APPROVED_SET
agent-workflow batch pause APPROVED_SET
agent-workflow release-clear TASK_ID
```

Batch pause stops future scheduling; already queued/running jobs use stop separately.
Batch limits persist across restarts. A release can only be cleared before it starts.
GitHub Enterprise requires an explicit matching `release-config --hostname HOST`.
