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
    def hook(event, **extra):
        payload=dict(session_id="claude-pilot",hook_event_name=event,cwd=str(project),
                     prompt="PRIVATE_PROMPT",tool_input={"value":"PRIVATE_TOOL"},tool_name="Bash")
        payload.update(extra)
        result=subprocess.run([sys.executable,str(Path(native.__file__).with_name("native_hook.py")),
            "--home",str(fixture.home),"--project",str(project)],input=json.dumps(payload),
            text=True,capture_output=True,timeout=5)
        assert (result.returncode,result.stdout,result.stderr)==(0,"","")
    hook("SessionStart")
    native.install(fixture.home,project,provider="codex")
    native.ingest(fixture.home,project,dict(session_id="codex-pilot",hook_event_name="PermissionRequest",cwd=str(project)),"codex")
    control=Path(fixture.temp.name)/"control"
    stop=threading.Event()
    def commands():
        while not stop.wait(.05):
            if control.exists():
                value=control.read_text()
                # fs.writeFile creates/truncates before writing; never consume an empty command.
                if not value:continue
                if value=="offline":fixture.fail=True
                elif value=="opencodeapproval":fixture.permissions=[{"sessionID":"ses_one","patterns":["PRIVATE_PATTERN"]}]
                elif value=="opencodeinput":fixture.permissions=[];fixture.questions=[{"sessionID":"ses_one","questions":["PRIVATE_QUESTION"]}]
                elif value.startswith("activity:"):
                    _, tool, phase = value.split(":")
                    hook("PreToolUse" if phase=="start" else "PostToolUse", tool_name=tool)
                elif value=="metricsretention":
                    for i in range(native.MAX_SESSIONS+1):
                        native.record(fixture.home,"claude",str(project),f"retention-{i}",str(project),"retention fixture","ended","SessionEnd")
                elif value=="metricsclock":
                    import time
                    c=native.database(fixture.home)
                    with c:c.execute("UPDATE history SET at=? WHERE event='SessionEnd' AND session_id IN (SELECT id FROM sessions WHERE native_id='metrics-fixture')",(time.time()-450,))
                    c.close()
                elif value=="metricsfixture":
                    import time
                    now=time.time()
                    sid=native.record(fixture.home,"claude",str(project),"metrics-fixture",str(project),"SYNTHETIC metrics","ended","SessionEnd")
                    c=native.database(fixture.home)
                    with c:
                        c.execute("DELETE FROM history WHERE session_id=?",(sid,))
                        for offset,event,state in [(-600,"UserPromptSubmit","running"),(-500,"PermissionRequest","approval"),(-400,"PostToolUseFailure","attention"),(-50,"SessionEnd","ended")]:
                            c.execute("INSERT INTO history(session_id,at,event,state,tool) VALUES(?,?,?,?,?)",(sid,now+offset,event,state,"Bash"))
                    c.close()
                elif value=="prunesession":
                    c=native.database(fixture.home)
                    with c:c.execute("DELETE FROM sessions WHERE provider='claude' AND native_id='claude-pilot'")
                    c.close()
                elif value=="prunehistory":
                    c=native.database(fixture.home)
                    with c:c.execute("DELETE FROM history WHERE session_id IN (SELECT id FROM sessions WHERE provider='claude' AND native_id='claude-pilot')")
                    c.close()
                elif value=="otherproject":
                    other=Path(fixture.temp.name)/"other-project";other.mkdir(exist_ok=True)
                    native.install(fixture.home,other)
                    native.ingest(fixture.home,other,dict(session_id="other-project-session",hook_event_name="SessionStart",cwd=str(other)))
                elif value=="subagentstart":hook("SubagentStart", agent_id="child-1", agent_type="reviewer")
                elif value=="subagentstop":hook("SubagentStop", agent_id="child-1")
                else:hook(value)
                control.unlink()
    thread=threading.Thread(target=commands,daemon=True);thread.start()
    try:
        server=fixture.server(handler(fixture.home,"fixture",read_only=True,observer=True))
        manifest=Path(fixture.temp.name)/"native.json"
        manifest.write_text(json.dumps({"url":f"http://127.0.0.1:{server.server_port}",
                                       "project":str(project),"control":str(control)}))
        result=subprocess.run(["node",os.environ.get("WORKFLOW_NATIVE_SCENARIO", "tests/native_scenarios.mjs"),str(manifest)],timeout=int(os.environ.get("WORKFLOW_NATIVE_TIMEOUT", "180"))).returncode
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
