"""Passive Claude hook: bounded metadata input, no decisions/output, always exit 0."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    os.umask(0o077)
    try:
        p = argparse.ArgumentParser()
        p.add_argument("--home", required=True)
        p.add_argument("--project", required=True)
        p.add_argument("--provider", choices=["claude", "codex"], default="claude")
        args = p.parse_args()
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return
        from agent_workflow.native import ingest
        ingest(args.home, args.project, json.loads(raw), args.provider)
    except (Exception, SystemExit):
        # Observability must never approve, reject or block the user's work.
        pass


if __name__ == "__main__":
    main()
