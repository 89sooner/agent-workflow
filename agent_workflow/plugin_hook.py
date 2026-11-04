"""Passive plugin bridge. Requires explicit project registration; never starts workers."""
import json
import os
from pathlib import Path
import sys


def main(provider=None):
    os.umask(0o077)
    try:
        provider = provider or sys.argv[1]
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("cwd"), str):
            return
        if not Path(payload["cwd"]).is_absolute():
            return
        project = str(Path(payload["cwd"]).resolve())
        from .native import ingest, ingest_opencode
        home = os.environ.get("AGENT_WORKFLOW_HOME")
        if provider == "opencode":
            ingest_opencode(home, project, payload)
        else:
            ingest(home, project, payload, provider, mode="plugin")
    except (Exception, SystemExit):
        # No stdout, decisions, prompt injection, approval or model invocation.
        pass


if __name__ == "__main__":
    main()
