import { spawn } from 'node:child_process';

// Keep delivery ordered and bounded. Observation must not delay an agent tool.
export function createBridge(directory) {
  const pending = [];
  let draining = false;
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
  return payload => {
    if (pending.length >= 64) return;
    pending.push(payload);
    if (!draining) void drain();
  };
}
