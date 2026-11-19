from pathlib import Path
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_workflow import check_advisor_models as models
from agent_workflow.core import WorkflowError


class CheckAdvisorModelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.cwd_record = self.base / "cwd"
        self.environment = patch.dict(os.environ, {"PATH": str(self.base) + os.pathsep + os.environ["PATH"]})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, name, code):
        path = self.base / name
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o700)

    def test_codex_uses_private_scratch_and_strict_isolation(self):
        self.executable("codex", f"""
import json, os, sys
from pathlib import Path
argv=sys.argv
assert sys.stdin.read() == '검사 후보'
assert '--ignore-user-config' in argv and '--ignore-rules' in argv and '--ephemeral' in argv
assert '--strict-config' in argv and '--no-daemon' in argv
assert 'permissions.advisor.network.enabled=false' in argv
assert 'permissions.advisor.filesystem={{":root"="deny"}}' in argv
assert 'web_search="disabled"' in argv
assert {{argv[i+1] for i,v in enumerate(argv) if v=='--disable'}} >= {{'shell_tool','apps','view_image','hooks','multi_agent','unified_exec','code_mode_host'}}
assert json.loads(Path(argv[argv.index('--output-schema')+1]).read_text())['additionalProperties'] is False
assert os.stat('.').st_mode & 0o077 == 0
Path({str(self.cwd_record)!r}).write_text(str(Path.cwd()))
Path(argv[argv.index('--output-last-message')+1]).write_text(json.dumps({{'groups':['release'],'reason':'병합 변경'}}))
""")
        self.assertEqual(models.ask("codex", "검사 후보", 5), {"groups": ["release"], "reason": "병합 변경"})
        self.assertFalse(Path(self.cwd_record.read_text()).exists())

    def test_claude_tools_disabled_and_both_formats(self):
        for key, result in (("structured_output", {"groups": [], "reason": "기본 검사"}),
                            ("result", json.dumps({"groups": [], "reason": "기본 검사"}))):
            with self.subTest(key=key):
                self.executable("claude", f"""
import json, sys
argv=sys.argv
assert '--restricted' in argv and '--safe-mode' in argv and '--strict-mcp-config' in argv
assert '--disable-slash-commands' in argv and '--no-session-persistence' in argv
assert argv[argv.index('--tools')+1] == '' and argv[argv.index('--permission-mode')+1] == 'dontAsk'
assert sys.stdin.read() == 'fixture'
print(json.dumps({{'subtype':'success',{key!r}:{result!r}}}))
""")
                self.assertEqual(models.ask("claude", "fixture", 5)["groups"], [])

    def test_invalid_answer_and_failures(self):
        for envelope in ({"subtype": "error", "structured_output": {"groups": [], "reason": "ok"}},
                         {"subtype": "success", "structured_output": {"groups": ["a"], "reason": "ok", "extra": 1}},
                         {"subtype": "success", "structured_output": {"groups": [1], "reason": "ok"}},
                         {"subtype": "success", "result": ""}):
            with self.subTest(envelope=envelope):
                self.executable("claude", "print(" + repr(json.dumps(envelope)) + ")")
                with self.assertRaises(WorkflowError):
                    models.ask("claude", "fixture", 3)
        self.executable("claude", "print('private prompt',file=__import__('sys').stderr);raise SystemExit(2)")
        with self.assertRaisesRegex(WorkflowError, "exit 2") as err:
            models.ask("claude", "fixture", 3)
        self.assertNotIn("private prompt", str(err.exception))
        with patch("shutil.which", return_value=None), self.assertRaisesRegex(WorkflowError, "PATH"):
            models.ask("codex", "fixture", 3)

    def test_timeout_stops_process_group_and_removes_scratch(self):
        marker = self.base / "child-survived"
        child = "import time; from pathlib import Path; time.sleep(1.6); Path(%r).touch()" % str(marker)
        self.executable("claude", f"""
import subprocess,sys,time
from pathlib import Path
Path({str(self.cwd_record)!r}).write_text(str(Path.cwd()))
subprocess.Popen([sys.executable,'-c',{child!r}])
time.sleep(30)
""")
        with self.assertRaisesRegex(WorkflowError, "시간|초"):
            models.ask("claude", "fixture", 1)
        self.assertFalse(Path(self.cwd_record.read_text()).exists())
        time.sleep(0.8)
        self.assertFalse(marker.exists())
