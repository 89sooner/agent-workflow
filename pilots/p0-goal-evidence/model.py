#!/usr/bin/env python3
"""Scripted native CLI double. Only the four trial actions can affect fixture files."""
import json
import os
from pathlib import Path
import sys


def main():
    prompt = sys.stdin.read()
    driver = Path(os.environ["P0_SCRIPT"])
    args = sys.argv
    if "--output-last-message" in args:
        count = int(os.environ["P0_CRITERIA"])
        answer = {"outcome": "pass", "summary": "scripted reviewer", "next_action": "finish", "findings": [],
                  "coverage": [{"criterion": i, "accepted": True, "evidence": "inspected the registered checks"}
                               for i in range(1, count + 1)]}
        Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(answer))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}))
        return
    actions = json.loads(driver.read_text())
    action = actions.pop(0)
    driver.write_text(json.dumps(actions))
    if action.get("needs_observation"):
        assert "operator observed sample 17" in prompt
    fixture = Path("web/fixtures/views.json")
    if "version" in action:
        data = {f"view-{i}": action["version"] for i in range(16)}
        if action.get("alternative"):
            data = dict(reversed(list(data.items())))
        fixture.write_text(json.dumps(data, indent=2 if action.get("alternative") else None))
        with driver.with_name("writes.jsonl").open("a") as log:
            print(json.dumps({"fixture": action["version"]}), file=log)
    if action.get("interrupt"):
        sys.exit(41)  # The write happened, but the native process never delivered final JSON.
    answer = {"criterion": action["id"], "outcome": action.get("outcome", "ready"),
              "summary": "scripted step", "next_action": "inspect current evidence"}
    print(json.dumps({"subtype": "success", "structured_output": answer,
                      "usage": {"input_tokens": 7, "output_tokens": 3}}))


if __name__ == "__main__":
    main()
