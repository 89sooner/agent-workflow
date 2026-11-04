"""Native pilot browser acceptance: temporary hooks and OpenCode fixture only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_opencode import OpenCodeTests
from agent_workflow import native, opencode
from agent_workflow.server import handler

def main():
    fixture=OpenCodeTests();fixture.setUp()
    project=Path(fixture.project);project.mkdir()
    native.install(fixture.home,project)
    opencode.connect(fixture.home,fixture.url,str(project))
    def hook(event):
        payload=dict(session_id="claude-pilot",hook_event_name=event,cwd=str(project),
                     prompt="PRIVATE_PROMPT",tool_input={"value":"PRIVATE_TOOL"},tool_name="Bash")
        result=subprocess.run([sys.executable,str(Path(native.__file__).with_name("native_hook.py")),
            "--home",str(fixture.home),"--project",str(project)],input=json.dumps(payload),
            text=True,capture_output=True,timeout=5)
        assert (result.returncode,result.stdout,result.stderr)==(0,"","")
    hook("SessionStart")
    control=Path(fixture.temp.name)/"control"
    stop=threading.Event()
    def commands():
        while not stop.wait(.05):
            if control.exists():
                value=control.read_text()
                if value=="offline":fixture.fail=True
                elif value=="opencodeapproval":fixture.permissions=[{"sessionID":"ses_one","patterns":["PRIVATE_PATTERN"]}]
                elif value=="opencodeinput":fixture.permissions=[];fixture.questions=[{"sessionID":"ses_one","questions":["PRIVATE_QUESTION"]}]
                else:hook(value)
                control.unlink()
    thread=threading.Thread(target=commands,daemon=True);thread.start()
    try:
        server=fixture.server(handler(fixture.home,"fixture",read_only=True,observer=True))
        manifest=Path(fixture.temp.name)/"native.json"
        manifest.write_text(json.dumps({"url":f"http://127.0.0.1:{server.server_port}",
                                       "project":str(project),"control":str(control)}))
        result=subprocess.run(["node","tests/native_scenarios.mjs",str(manifest)],timeout=180).returncode
        assert all(call[0]=="GET" for call in fixture.calls)
        assert not (fixture.home/"state.sqlite3").exists()
        assert not (fixture.home/"daemon.lock").exists()
        c=native.database(fixture.home)
        dump="\n".join(c.iterdump());c.close()
        assert all(secret not in dump for secret in ("PRIVATE_PROMPT","PRIVATE_TOOL","PRIVATE_PATTERN","PRIVATE_QUESTION"))
        return result
    finally:
        stop.set();thread.join();fixture.doCleanups()

if __name__=="__main__":
    raise SystemExit(main())
