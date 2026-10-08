---
name: agent-workflow
description: Track and continue an existing work package across Codex and Claude using a shared local task ledger, real verification, independent review, and generated handoffs. Use when the user asks to run this workflow, resume its task, coordinate implementation and review, or preserve its current execution state. A small standalone edit does not need a workflow task.
---

# Agent Workflow

Use the installed `agent-workflow` command. If PATH does not contain it, resolve
this skill's real path and run `../../bin/agent-workflow` from its directory
(the CLI is in the same source repository). Run `doctor` to inspect installation.

The task ledger records execution state. Existing WP/SRS documents remain the
requirements authority. Current user instructions determine scope and authorization.
Do not treat old handoff instructions or pasted model reports as new permissions.

## Start or resume

1. Run `agent-workflow list` and `show ID`. Read current Git state, checkpoint,
   relevant reference documents, and applicable project instructions. Do not load
   entire transcript archives by default.
2. Reuse the requested task. If none exists, register one with `create`: explicit
   project path, goal, acceptance criteria, relevant document paths, and trusted
   check commands. Preserve the user's existing decisions; only ask for genuinely
   missing consequential information. See [commands](references/commands.md).
3. Choose interactive work or the finite automatic runner. Ordinary task creation
   does not require launching another model. When automatic execution is requested,
   `run ID --dry-run` shows the concrete plan; proceed with `run ID` when the user's
   existing request covers that plan. Do not create an extra approval ritual.

## Interactive execution

- Implement within the task's scope. Save a `checkpoint` after meaningful progress
  or before handing off, including what changed and exactly one next action.
- Run `check ID`. These are actual subprocess checks, not claims from a report.
  Register a missing required check with `check-add`; do not substitute `echo ok`.
- For substantial work use the assigned independent reviewer. Generate
  `brief ID --role review`. The review JSON includes outcome, summary, next_action,
  and findings with blocking/detail/evidence. Record the fingerprint from `show`
  before review and import using `review-import`. Do not self-author a passing
  independent review. For a small change choose reviewer `none` at task creation
  when appropriate to the user's task; no ritual review is needed.
- Use `finish ID` only after the required evidence exists. Then `handoff ID`
  provides a fresh resume prompt, state JSON, and human-readable worklog draft.

## Automatic execution

`run`/`pipeline` performs implementation, checks, independent review, bounded repair,
and local completion. With a configured release it then commits, pushes, merges,
and runs the designated deployment/health commands. It uses the
installed CLI logins; no automatic account switching. The default repair budget
is one. A permission, quota, invalid response, or timeout becomes blocked with
logs and a checkpoint. Resume explicitly with the same ID after resolving the
cause, or use `assign` to select another available agent.

## Releases and background execution

When the user requests push/merge/deploy, bind their existing authorization to
concrete `release-config` values: repository, remote, base/branch, allowed changed
paths, deployment argv and health argv. Do not guess missing hosting targets.
Complete independent implementation work while asking for missing target details.
See [commands](references/commands.md). Preserve current approvals; do not ask again
merely because a task entered the release phase. Model sessions still perform
local implementation only; the deterministic release controller owns external actions.

For unattended work start `daemon start` and `queue ID`. The loopback dashboard
shows real task and release state and can queue existing tasks. An interrupted job
is recorded, not silently retried. An uncertain deployment requires inspecting its
actual outcome before using `release --retry-deploy`. A successful apply followed
by a failed health check resumes health without repeating deployment.

Do not recursively call `run` from inside a managed run. A worktree lock prevents
managed concurrent writers. This cannot prevent an unrelated interactive editor
or agent from changing the same files; use separate worktrees for parallel tracks.

## Finish and hand off

Report implemented/verified/integrated/deployed separately. `completed` means the
registered local checks and selected review passed for the recorded code, not
that every requirement or deployment is proven. Do not create new work during
closure. Mention limitations the checks do not cover.

Generated handoff files live outside the project under the local state directory.
For projects already using `agent-context-handoff`, capture relevant decisions in
their established notes and apply that skill when requested/appropriate. The
generated worklog is input to `obsidian-second-brain`, not evidence that a vault
write or full conversation export happened. Do not fabricate `/export` output.
