"""Classify agent transport failures; never retry permissions or release effects."""
from pathlib import Path
import json
import re

from .core import WorkflowError, now


class AgentFailure(WorkflowError):
    def __init__(self, kind, message, directory=None):
        super().__init__(message)
        self.kind = kind
        self.directory = str(directory) if directory else None


def classify(outcome, directory):
    fragments = []
    for name in ("stderr.log", "stdout.log"):
        try:
            with (Path(directory) / name).open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 16384))
                tail = stream.read().decode(errors="replace")
                if name == "stderr.log":
                    fragments.append(tail)
                else:
                    # stdout contains agent tool/file output. Classify only CLI
                    # error envelopes, never source text or ordinary assistant text.
                    try:
                        entries = [json.loads(tail)]
                    except ValueError:
                        entries = []
                        for line in tail.splitlines():
                            try:
                                entries.append(json.loads(line))
                            except ValueError:
                                continue
                    for item in entries:
                        if isinstance(item, dict) and (item.get("type") in {"error", "turn.failed"} or item.get("is_error") is True):
                            fragments.append(json.dumps({key: item[key] for key in ("error", "message", "result", "subtype") if key in item}))
        except OSError:
            pass
    text = "\n".join(fragments).lower()
    for kind, pattern in (
        ("authentication", r"unauthorized|authentication[_ ]error|invalid[_ ]api[_ ]key|not logged in|please log in|login required"),
        ("permission", r"permission denied|permission[_ ]error|operation not permitted|access denied|forbidden"),
        ("quota", r"insufficient_quota|(?:usage|session|weekly) limit|quota exceeded|out of credits|insufficient credits|credit balance.{0,30}too low|rate[_ ]?limit|too many requests|\b429\b"),
    ):
        if re.search(pattern, text):
            return kind
    if outcome.get("timed_out"):
        return "timeout"
    if re.search(r"econnreset|etimedout|connection reset|temporarily unavailable|overloaded_error|server_error|\b50[234]\b", text):
        return "transient"
    return "process_failure"


def record_failure(store, task, failure, role):
    task["failure"] = {"kind": failure.kind, "role": role,
                       "directory": failure.directory, "at": now(), "message": str(failure)}
    store.save(task, "agent_failed", task["failure"])


def take_recovery(store, task, budget, failure, action):
    if budget["used"] >= budget["limit"]:
        raise failure
    budget["used"] += 1
    record = {"attempt": budget["used"], "limit": budget["limit"], "kind": failure.kind,
              "action": action, "directory": failure.directory, "at": now()}
    task.setdefault("recoveries", []).append(record)
    store.save(task, "recovery_started", record)
