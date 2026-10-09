"""Opt-in real browser acceptance through profiles, services and check evidence."""
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_workflow.core import Store, complete, git
from agent_workflow.profiles import save
from agent_workflow.runner import run_checks
from agent_workflow.workspaces import create_from_profile
from agent_workflow.closure import finalize


def main():
    source = Path(__file__).resolve().parents[1]
    output = source / "artifacts/browser-integration"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="workflow-browser-") as tmp:
        root = Path(tmp); project = root / "project"; project.mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        page = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>Browser fixture</title><label>이름 <input id="name"></label><button id="submit">확인</button><p id="result"></p><script>document.querySelector("#submit").onclick=()=>document.querySelector("#result").textContent="완료: "+document.querySelector("#name").value;</script></html>'
        (project / "index.html").write_text(page)
        scenario = {"url": f"http://127.0.0.1:{port}/", "steps": [
            {"action": "fill", "selector": "#name", "value": "워크플로"},
            {"action": "click", "selector": "#submit"},
            {"action": "text", "selector": "#result", "value": "완료: 워크플로"}]}
        (project / "scenario.json").write_text(json.dumps(scenario, ensure_ascii=False))
        git(project, "init", "-q"); git(project, "add", ".")
        git(project, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "browser fixture")
        store = Store(root / "state")
        try:
            save(store, "browser", {"project": str(project), "reviewer": "none", "allowed_paths": ["index.html"],
                                    "checks": [[str(source / "bin/agent-workflow"), "browser-check", "--file", "scenario.json"]],
                                    "services": [{"name": "web", "argv": [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], "port": port, "ready_url": scenario["url"]}],
                                    "exports": {"directory": str(root / "dailywork"), "timezone": "Asia/Seoul"}})
            task = create_from_profile(store, "BROWSER-PASS", "browser", "실제 버튼과 입력 검사", ["입력 후 결과 표시"])
            with store.lock(task["project"]) as fd:
                assert run_checks(store, task, 30, fd)
                complete(store, task)
            result = finalize(store, task)
            check = task["verification"]["results"][0]
            for artifact in check["artifacts"]:
                shutil.copy2(artifact, output / Path(artifact).name)
            exported = Path(result["export"]["path"])
            shutil.copy2(exported, output / exported.name)
            assert all(s["state"] == "stopped" for s in task["active_services"])
            from agent_workflow.server import identity
            assert identity(task["active_services"][0]["pid"]) is None

            blocked = create_from_profile(store, "BROWSER-EXTERNAL", "browser", "외부 요청 차단 검사", ["격리 화면에서 외부 요청은 실패"])
            fixture = Path(blocked["project"]) / "index.html"
            fixture.write_text(page.replace('</script>', 'fetch("https://production.example.invalid/should-not-run").catch(()=>{});</script>'))
            with store.lock(blocked["project"]) as fd:
                assert not run_checks(store, blocked, 30, fd)
            proof = next(Path(x) for x in blocked["verification"]["results"][0]["artifacts"] if x.endswith("browser-proof.json"))
            failure = json.loads(proof.read_text())
            assert failure["passed"] is False
            assert failure["externalRequests"] == ["https://production.example.invalid"]
            assert all(s["state"] == "stopped" for s in blocked["active_services"])
            shutil.copy2(proof, output / "blocked-external-proof.json")
            report = {"positive_check": "PASS", "external_request_blocking": "PASS", "services_cleaned": True, "same_port_reused": True,
                      "worklog_exported": True, "actual_model_calls": 0, "external_network_requests": 0}
            (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
        finally:
            store.close()


if __name__ == "__main__":
    main()
