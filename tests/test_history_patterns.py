from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow.cli import dispatch, parser
from agent_workflow.history_patterns import default_sources, summarize


class PatternTests(unittest.TestCase):
    def test_cross_profile_dedup_and_same_session_co_use(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            single, duplicate = base / "input", base / "copy"
            def claude(ts, text, sid="private-session"):
                return dict(timestamp=ts, display=text, sessionId=sid, pastedContents={"1": "secret"})
            rows = [claude(1000, "/agent-context-handoff"),
                    claude(2000, "/export /private/test/pr"),
                    claude(2000, "/export /private/test/pr"),
                    claude(3000, "/private-command secret", "another"),
                    dict(ts=1, text="다음 단계 검증 private", session_id="private-session")]
            single.write_text("".join(json.dumps(r) + "\n" for r in rows) + "{")
            duplicate.write_text(json.dumps(rows[0]))
            result = dispatch(parser().parse_args(["patterns", "--source", str(single), "--source", str(duplicate)]))
            self.assertEqual(result["duplicate_rows"], 2)
            self.assertEqual(result["invalid_rows"], 1)
            c = result["providers"]["claude"]
            self.assertEqual((c["inputs"], c["sessions"]), (3, 2))
            self.assertEqual(c["command_pairs_sessions"], {"agent-context-handoff + export": 1})
            self.assertNotIn("검증·리뷰", c["topics"])
            self.assertNotIn("Git·릴리스", c["topics"])
            self.assertEqual(result["providers"]["codex"]["topics"]["검증·리뷰"]["inputs"], 1)
            output = json.dumps(result)
            for token in ("secret", "private", "another"):
                self.assertNotIn(token, output)

    def test_custom_profiles_preserve_default_histories(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            paths = []
            for name in (".codex", ".claude", ".claude-team", "custom-codex"):
                path = home / name / "history.jsonl"
                path.parent.mkdir()
                path.touch()
                paths.append(path)
            with patch("pathlib.Path.home", return_value=home), patch.dict(
                "os.environ", {"CODEX_HOME": str(paths[3].parent), "CLAUDE_CONFIG_DIR": str(paths[2].parent)}
            ):
                self.assertCountEqual(default_sources(), paths)

    def test_day_window_and_bad_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "history"
            rows = [dict(ts=ts, session_id="s", text="text") for ts in (0, 86399, 86400)]
            path.write_text("\n".join(map(json.dumps, [*rows, [], {"ts": 2}, dict(rows[0], ts=True)])))
            d = date(1970, 1, 1)
            r = summarize([path], since=d, until=d)
            self.assertEqual(r["providers"]["codex"]["inputs"], 2)
            self.assertEqual((r["outside_date_range"], r["invalid_rows"]), (1, 3))
