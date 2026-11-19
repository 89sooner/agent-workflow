"""Count workflow signals in local CLI input histories without retaining prompt text."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations
import json
import os
from pathlib import Path
import re

from .core import WorkflowError


# Only these public labels can appear in output. Never emit arbitrary slash arguments.
COMMANDS = {
    "agent-context-handoff", "agent-workflow", "obsidian-second-brain", "export",
    "copy", "clear", "model", "effort", "usage", "resume", "rename", "login",
    "rate-limit-options", "compact", "goal", "grill-me", "build-srs-prd-env",
}
COMMAND = re.compile(r"^/([\w:-]+)(?=\s|$)")
RULES = {
    "인계·문맥 복원": r"handoff|인수인계|인계|핸드오프|컨텍스트|다음 세션|새 세션",
    "기록·문서화": r"옵시디언|obsidian|second-brain|작업\s*일지|worklog|문서화|기록해|기록하",
    "계속·다음 단계": r"진행|계속|이어서|이어가|다음\s*(?:작업|단계)|continue|proceed",
    "기획·설계": r"기획|설계|요구사항|계획|로드맵|\b(?:prd|srs)\b|work package|작업\s*패키지",
    "조사·분석": r"조사|리서치|분석|research",
    "구현·수정": r"구현|개발|고쳐|수정|리팩터|리팩토|implement|refactor",
    "검증·리뷰": r"검증|검토|테스트|리뷰|재현|\b(?:tests?|testing|review|verify)\b",
    "장애·원인 조사": r"버그|원인|오류|에러|디버[깅그]|장애|debug|regression",
    "Git·릴리스": r"커밋|푸시|풀리퀘|머지|배포|릴리[스즈]|병합|\b(?:commit|push|pr|deploy|merge)\b",
    "상태 확인": r"어디까지|현황|남은|상태|진척|모니터링|진행률|status",
    "화면·브라우저": r"화면|브라우저|스크린샷|playwright|\bui\b|\bux\b",
    "데이터·백테스트": r"백테스트|승률|손익|거래|매매|backtest",
    "도구·자동화": r"자동화|스킬|플러그인|자동으로|automation|plugin|\bskill\b",
}
MATCHERS = {name: re.compile(pattern, re.I) for name, pattern in RULES.items()}


def default_sources():
    home = Path.home()
    profiles = [home / ".codex", home / ".claude", *sorted(home.glob(".claude-*"))]
    profiles.extend(Path(value).expanduser() for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR")
                    if (value := os.environ.get(key)))
    paths = dict.fromkeys((profile / "history.jsonl").resolve() for profile in profiles)
    return [path for path in paths if path.is_file()]


def input_row(row):
    if not isinstance(row, dict):
        return None
    if "display" in row:
        provider, message, sid, ts = "claude", row.get("display"), row.get("sessionId"), row.get("timestamp")
        scale = 1000
    else:
        provider, message, sid, ts = "codex", row.get("text"), row.get("session_id"), row.get("ts")
        scale = 1
    if not (isinstance(message, str) and message.strip() and isinstance(sid, str) and sid
            and type(ts) in (int, float)):
        return None
    day = datetime.fromtimestamp(ts / scale, timezone.utc).date()
    return provider, message, sid, ts, day


def summarize(sources=None, *, since=None, until=None):
    if since and until and since > until:
        raise WorkflowError("시작 날짜가 종료 날짜보다 늦습니다.")
    paths = list(dict.fromkeys(p.expanduser().resolve() for p in (sources or default_sources())))
    if not paths:
        raise WorkflowError("입력 이력이 없습니다. --source로 history.jsonl을 지정하세요.")
    sessions = defaultdict(dict)
    counts = defaultdict(Counter)
    commands = defaultdict(Counter)
    months = defaultdict(Counter)
    days = defaultdict(set)
    tag_counts = defaultdict(Counter)
    seen = set()
    invalid = filtered = duplicates = 0
    for path in paths:
        with path.open("rb") as stream:
            for line in stream:
                try:
                    event = input_row(json.loads(line))
                except (ValueError, OverflowError, OSError):
                    event = None
                if event is None:
                    invalid += 1
                    continue
                provider, message, sid, ts, day = event
                if (since and day < since) or (until and day > until):
                    filtered += 1
                    continue
                key = (provider, sid, ts, sha256(message.encode("utf-8", "surrogatepass")).digest())
                if key in seen:
                    duplicates += 1
                    continue
                seen.add(key)
                counts[provider]["inputs"] += 1
                days[provider].add(day)
                months[provider][day.isoformat()[:7]] += 1
                surface = message.strip()
                match = COMMAND.match(surface)
                if match:
                    command = match[1] if match[1] in COMMANDS else "other"
                    commands[provider][command] += 1
                    # Export paths and copied arguments can contain unrelated project keywords.
                    surface = match[1] if command != "goal" else surface[match.end():]
                else:
                    command = None
                tags = {name for name, rule in MATCHERS.items() if rule.search(surface)}
                session_tags, session_commands = sessions[provider].setdefault(sid, (set(), set()))
                session_tags.update(tags)
                if command:
                    session_commands.add(command)
                tag_counts[provider].update(tags)
                if not tags:
                    counts[provider]["unclassified_inputs"] += 1

    providers = {}
    for provider, by_id in sorted(sessions.items()):
        tag_sessions, cmd_sessions, co_use = Counter(), Counter(), Counter()
        focus = {"agent-context-handoff", "export", "obsidian-second-brain", "copy"}
        for tags, cmds in by_id.values():
            tag_sessions.update(tags)
            cmd_sessions.update(cmds)
            co_use.update(" + ".join(pair) for pair in combinations(sorted(cmds & focus), 2))
        providers[provider] = {
            "inputs": counts[provider]["inputs"], "sessions": len(by_id),
            "first_day": min(days[provider]).isoformat(), "last_day": max(days[provider]).isoformat(),
            "active_days": len(days[provider]), "monthly_inputs": dict(sorted(months[provider].items())),
            "unclassified_inputs": counts[provider]["unclassified_inputs"],
            "topics": {k: {"inputs": n, "sessions": tag_sessions[k]}
                       for k, n in sorted(tag_counts[provider].items(), key=lambda it: (-it[1], it[0]))},
            "commands": {k: {"inputs": n, "sessions": cmd_sessions[k]} for k, n in commands[provider].most_common()},
            "command_pairs_sessions": dict(co_use.most_common()),
        }
    return {"source_files": len(paths), "invalid_rows": invalid, "outside_date_range": filtered,
            "duplicate_rows": duplicates, "providers": providers}
