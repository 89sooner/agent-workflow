import { spawn } from 'node:child_process';

// One export: OpenCode treats every exported function as a plugin initializer.
export const AgentWorkflowObserver = async ({ directory, client }) => {
  // Bounded FIFO prevents delayed writes reversing session transitions.
  const pending = [];
  let draining = false;
  const send = (payload) => {
    if (pending.length >= 64) return; // Observability must not backpressure the agent.
    pending.push(payload);
    if (!draining) void drain();
  };
  async function drain() {
    draining = true;
    while (pending.length) {
      const payload = pending.shift();
      await new Promise(resolve => {
        let child, timer;
        const done = () => { clearTimeout(timer); resolve(); };
        try {
          child = spawn(process.env.AGENT_WORKFLOW_PYTHON || 'python3',
            ['-P', '-m', 'agent_workflow.plugin_hook', 'opencode'],
            { stdio: ['pipe', 'ignore', 'ignore'], windowsHide: true });
          timer = setTimeout(() => { child.kill(); done(); }, 2500);
          child.on('error', done);
          child.on('close', done);
          child.stdin.on('error', () => {});
          child.stdin.end(JSON.stringify({ ...payload, cwd: directory }));
        } catch { done(); }
      });
    }
    draining = false;
  }
  // The first session.created can precede OpenCode's event subscription startup.
  // One read-only metadata snapshot fills that gap, without asserting live activity.
  const bootstrap = setTimeout(async () => {
    try {
      const result = await client?.session.list({ query: { directory, limit: 100 } });
      for (const session of (Array.isArray(result?.data) ? result.data : []).slice(0, 100)) {
        if (session.directory !== directory) continue;
        send({session_id: session.id, hook_event_name: 'session.discovered', parent: session.parentID});
      }
    } catch { /* An unavailable observer must not affect the agent. */ }
  }, 500);
  bootstrap.unref?.();
  return {
    event: async ({ event }) => {
      const p = event.properties || {};
      const types = ['session.created', 'session.deleted', 'session.status', 'session.idle',
        'session.error', 'permission.asked', 'permission.replied', 'question.asked',
        'question.replied', 'question.rejected'];
      if (!types.includes(event.type)) return;
      const id = p.sessionID || p.info?.id;
      if (typeof id !== 'string' || id.length > 160) return;
      send({session_id: id, hook_event_name: event.type,
        status: p.status?.type, parent: p.info?.parentID});
    },
    'tool.execute.before': async (input) => {
      send({session_id: input.sessionID, hook_event_name: 'tool.execute.before', tool_name: input.tool});
    },
    'tool.execute.after': async (input) => {
      send({session_id: input.sessionID, hook_event_name: 'tool.execute.after', tool_name: input.tool});
    },
  };
};
