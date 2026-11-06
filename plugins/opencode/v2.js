import { createBridge } from './bridge.js';

// Explicit V2 entrypoint. Keep the function export in index.js for older V1 hosts.
export default {
  id: 'agent-workflow-observer',
  async setup(ctx) {
    const directory = ctx.location.directory;
    const send = createBridge(directory);
    const controller = new AbortController();
    const known = new Map();
    const remember = (id, parent) => {
      if (known.size >= 500 && !known.has(id)) known.delete(known.keys().next().value);
      known.set(id, { parent });
    };
    const types = ['session.created', 'session.deleted', 'session.moved', 'session.status', 'session.idle',
      'session.execution.started', 'session.execution.succeeded',
      'session.execution.failed', 'session.execution.interrupted',
      'permission.asked', 'permission.replied',
      'form.created', 'form.replied', 'form.cancelled'];
    // The stream contains metadata for multiple locations. Do not misattribute it.
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ types, signal: controller.signal })) {
          const p = event.data || {};
          const info = p.info || p.session;
          const id = p.sessionID || info?.id;
          if (typeof id !== 'string' || id.length > 160) continue;
          if (event.type === 'session.moved') {
            const previous = known.get(id);
            known.delete(id);
            if (previous) send({ session_id: id, hook_event_name: event.type, parent: previous.parent });
            continue;
          }
          if (event.location?.directory && event.location.directory !== directory) continue;
          if (!event.location?.directory && !known.has(id)) {
            // Some execution events omit location. Resolve metadata, never a transcript.
            try {
              const result = await ctx.session.get({ sessionID: id });
              if (result?.data?.location?.directory !== directory) continue;
              remember(id, result.data.parentID);
            } catch { continue; }
          }
          const parent = p.parentID || info?.parentID || known.get(id)?.parent;
          remember(id, parent);
          send({ session_id: id, hook_event_name: event.type,
            status: p.status?.type, parent });
        }
      } catch { /* Observer failures must not stop work. */ }
    })();
    for (const [hook, event] of [['execute.before', 'tool.execute.before'],
      ['execute.after', 'tool.execute.after']]) {
      await ctx.tool.hook(hook, input => {
        if (typeof input.sessionID !== 'string') return;
        send({ session_id: input.sessionID, hook_event_name: event, tool_name: input.tool });
      });
    }
    try {
      const result = await ctx.session.list({ location: { directory }, limit: 100 });
      for (const session of (Array.isArray(result?.data) ? result.data : []).slice(0, 100)) {
        if ((session.location?.directory || session.directory) !== directory) continue;
        remember(session.id, session.parentID);
        send({ session_id: session.id, hook_event_name: 'session.discovered', parent: session.parentID });
      }
    } catch { /* An unavailable snapshot does not establish live activity. */ }
    return () => controller.abort();
  },
};
