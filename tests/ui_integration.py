"""Exercise the production React management shell with disposable ledgers and synthetic model CLIs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack, closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from agent_workflow import connections, profiles
from agent_workflow.check_advisor import recommend
from agent_workflow.core import Store, create_task, git
from agent_workflow.daemon import init_queue, run_daemon
from agent_workflow.release import configure
from agent_workflow.runner import run_checks
from agent_workflow.server import handler
from react_browser_fixture import prepare_workspace


def main():
    with tempfile.TemporaryDirectory(prefix="workflow-react-management-") as tmp, ExitStack() as stack:
        root = Path(tmp)
        f = prepare_workspace(root)
        home, project = Path(f["home"]), Path(f["project"])
        with closing(Store(home)) as store:
            init_queue(store)
            profiles.save(store, "fixture-project", {
                "project": str(project), "checks": [[sys.executable, "-c", "print('base check')"]],
                "check_groups": {"ui": [[sys.executable, "-c", "print('ui check')"]],
                                 "docs": [[sys.executable, "-c", "print('docs check')"]]},
            })
            profiles.save(store, "project-ui-goal", {
                "project": str(project), "allowed_paths": ["app.py"],
                "checks": [[sys.executable, "-c", "import app; assert app.VALUE == 1"]],
                "goal_mode": {"version": 1, "scope": "Local UI fixture", "instructions": ["app.py"],
                              "input_extensions": []},
            })
            from agent_workflow import katakuri_goal as kg
            kb = root / "katakuri-band-fixture"
            kb.mkdir()
            git(kb, "init", "-q")
            for name in ["AGENTS.md", kg.DIRECTIVE, kg.HANDOFF, "app.py"]:
                path = kb / name
                path.parent.mkdir(exist_ok=True)
                path.write_text("fixture")
            git(kb, "add", ".")
            git(kb, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
            profiles.save(store, "kb-ui", {"project": str(kb), "allowed_paths": ["app.py"],
                          "checks": [[sys.executable, "-c", "print('offline')"]]})
            waiting = kg.create(store, "KG-INPUT", "kb-ui", "외부 근거 확인", ["관측 기록이 있다"])
            waiting["katakuri_goal"]["items"][0].update(state="waiting", next_action="정제된 기록 필요")
            waiting["katakuri_goal"]["phase"] = "waiting"
            waiting["status"] = "blocked"
            store.save(waiting, "fixture_goal_waiting")
            report = (
                '<testsuites><testsuite name="web" tests="2" skipped="1">'
                '<testcase classname="web" name="fixture"/>'
                '<testcase classname="web" name="bundle"><skipped/></testcase>'
                '</testsuite></testsuites>'
            )
            (kb / "fixture-report.xml").write_text(report)
            git(kb, "add", "fixture-report.xml")
            git(kb, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "test input")
            emit_report = [sys.executable, "-c",
                           "import os; from pathlib import Path; "
                           "(Path(os.environ['WORKFLOW_EVIDENCE_DIR']) / 'junit.xml').write_bytes(Path('fixture-report.xml').read_bytes())"]
            criteria_checks = {"fixture 일치": {"check": emit_report, "testcase": "web::fixture"},
                               "빌드된 번들 검사": {"check": emit_report, "testcase": "web::bundle"}}
            profiles.save(store, "kb-ui-checks", {"project": str(kb), "allowed_paths": ["app.py"],
                          "checks": [emit_report], "check_inputs": ["fixture-report.xml"], "criterion_checks": criteria_checks})
            proof = kg.create(store, "KG-PROOF", "kb-ui-checks", "조건별 검사 결과 확인", list(criteria_checks))
            with store.lock(proof["project"]) as fd:
                run_checks(store, proof, 10, fd)
            f["proof_project"] = proof["project"]
            checked = create_task(store, "UI-RECOVERY", project, "검사 후 독립 리뷰 재개",
                                  ["app.py가 존재한다"], [],
                                  [[sys.executable, "-c", "from pathlib import Path; assert Path('app.py').is_file()"]],
                                  "claude", "codex", "fixture")
            with store.lock(project) as fd:
                assert run_checks(store, checked, 10, fd)
            remote = root / "bare"
            remote.mkdir()
            git(remote, "init", "--bare", "-q")
            git(project, "remote", "add", "test", str(remote))
            configure(store, "OBS-TRACE", "test", git(project, "branch", "--show-current"),
                      "work/synthetic", ["app.py"], mode="local")
            # Persist an applied fixture deployment with a failing health probe.
            # The UI must show each receipt independently even without a live target.
            partial = create_task(store, "UI-HEALTH", project, "Synthetic partial release", ["read receipts"],
                                  [], [], "codex", "none", "fixture")
            partial["release"] = {
                "stage":"deploying", "config":{"deploy":["fixture-apply"], "health":["fixture-probe"]},
                "merge_commit":"a" * 40, "deployment_state":"applied",
                "health_result":{"exit_code":7, "timed_out":False},
            }
            store.save(partial, "fixture_partial_release")

        other = root / "foreign-ledger"
        with closing(Store(other)) as foreign:
            create_task(foreign, "UI-FOREIGN", f["other_project"], "읽기 전용 작업", ["view only"],
                        [], [], "codex", "none", "fixture")
            create_task(foreign, "UI-RECOVERY", f["other_project"], "다른 원장의 같은 ID",
                        ["별도의 재개 조건"], [], [], "codex", "none", "foreign fixture")
            # Force discovery to order the foreign ledger first, as in the reported failure.
            with foreign.db:
                task = foreign.get("UI-FOREIGN")
                task["updated_at"] = "2099-01-01T00:00:00+00:00"
                foreign.db.execute("UPDATE tasks SET data=? WHERE id=?", (json.dumps(task), task["id"]))
            before = list(foreign.db.iterdump())

        connections.connect(home, f["other_project"], ledgers=[other], include_default=False,
                            include_temporary=False, include_processes=False)
        criteria = ["표시값 검증", "선택한 검사 통과"]
        with patch("agent_workflow.check_advisor_models.ask",
                   return_value={"groups": ["ui"], "reason": "Synthetic fixture"}):
            saved = recommend(home, "fixture-project", criteria, reviewer="none", record_result=True)
        f.update(primary=connections.ledger_id(home), foreign=connections.ledger_id(other),
                 receipt=saved["recommendation_id"], criteria=criteria,
                 start_worker=str(root / "start-worker"), signal_agent=f["release_file"])
        stack.enter_context(patch.dict(os.environ, {
            "PATH": f["bin"] + os.pathsep + os.environ["PATH"],
            "WORKFLOW_SAAS_RELEASE": f["release_file"], "PYTHONDONTWRITEBYTECODE": "1",
        }))

        # Use a real model-shaped response while ensuring the browser cannot contact a provider.
        advisor = stack.enter_context(patch("agent_workflow.check_advisor_models.ask",
                                            return_value={"groups": ["docs"], "reason": "실습 문서 변경"}))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(home, "fixture-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.callback(thread.join)
        stack.callback(server.server_close)
        stack.callback(server.shutdown)
        stop = threading.Event()
        def work():
            while not stop.wait(.05):
                if Path(f["start_worker"]).exists():
                    run_daemon(home, stop)
                    return
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        stack.callback(worker.join, 15)
        stack.callback(stop.set)
        f["url"] = f"http://127.0.0.1:{server.server_port}"
        manifest = root / "fixture.json"
        manifest.write_text(json.dumps(f))
        try:
            subprocess.run(["node", "tests/ui_scenarios.mjs", str(manifest)], cwd=SOURCE,
                           check=True, timeout=240)
        finally:
            Path(f["signal_agent"]).touch()
        with closing(Store(home, read_only=True)) as store:
            task = store.get("UI-NEW")
            assert task["profile"]["recommendation_id"] == f["receipt"]
            assert task["verification"]["passed"] and task["status"] == "completed"
            assert store.db.execute("SELECT count(*) FROM check_recommendations").fetchone()[0] == 1
        with closing(Store(other, read_only=True)) as foreign:
            assert list(foreign.db.iterdump()) == before, "the foreign ledger changed"
        assert advisor.call_count == 1, "only the explicitly confirmed UI request should invoke a model"


if __name__ == "__main__":
    main()
