"""A local, explicitly labeled demonstration with a real bare Git remote and deployment."""
from pathlib import Path
import sys

from .core import WorkflowError, complete, create_task, git
from .release import configure
from .runner import run_checks


def setup_demo(store):
    task_id = "DEMO-RELEASE"
    if any(t["id"] == task_id for t in store.all()):
        return {"id": task_id, "note": "기존 시범 작업을 유지합니다."}
    root = store.home / "demo"
    if root.exists():
        raise WorkflowError("시범 폴더가 이미 존재합니다. 기존 내용을 확인하세요.")
    project, remote = root / "project", root / "remote.git"
    project.mkdir(parents=True)
    git(root, "init", "--bare", str(remote))
    git(project, "init", "-b", "main")
    git(project, "config", "user.name", "Agent Workflow Demo")
    git(project, "config", "user.email", "demo@example.invalid")
    git(project, "remote", "add", "origin", str(remote))
    (project / "site.html").write_text("<h1>Baseline</h1>\n")
    (project / "check.py").write_text("from pathlib import Path\ns=Path('site.html').read_text()\nassert '로컬 시범 배포' in s and '{{revision}}' in s\nprint('site content verified')\n")
    (project / "deploy.py").write_text('''import json, os, sys
from pathlib import Path
target=Path(sys.argv[2]); revision=os.environ['WORKFLOW_REVISION']
if sys.argv[1]=='deploy':
    target.mkdir(parents=True, exist_ok=True)
    content=Path('site.html').read_text().replace('{{revision}}',revision)
    tmp=target/'index.tmp'; tmp.write_text(content); tmp.replace(target/'index.html')
    (target/'revision.json').write_text(json.dumps({'revision':revision}))
    print('deployed '+revision)
else:
    assert json.loads((target/'revision.json').read_text())['revision']==revision
    assert revision in (target/'index.html').read_text()
    print('healthy '+revision)
''')
    git(project, "add", ".")
    git(project, "commit", "-m", "Local demonstration baseline")
    git(project, "push", "origin", "main")
    t = create_task(store, task_id, project, "로컬 시범 사이트를 커밋·푸시·병합·배포", ["배포된 페이지에 병합 커밋 SHA가 표시된다"], [],
                    [[sys.executable, "check.py"]], "claude", "none", "시범용 로컬 저장소와 배포 디렉터리에 한함")
    (project / "site.html").write_text('''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>로컬 시범 배포</title><link rel="stylesheet" href="/static/styles.css"><body><main><h1>로컬 시범 배포가 완료됐습니다.</h1><p>실제 Git 커밋 → 로컬 원격 푸시 → main 병합 → 배포 → 상태 확인</p><p>배포된 커밋: <code>{{revision}}</code></p><p>이 페이지는 외부 서비스가 아닌 이 WSL2의 시범 배포입니다.</p><a href="/">대시보드로 돌아가기</a></main></body></html>\n''')
    with store.lock(project) as fd:
        if not run_checks(store, t, 30, fd):
            raise WorkflowError("시범 파일 검사 실패")
        complete(store, t)
    configure(store, task_id, "origin", "main", "workflow/demo-release", ["site.html"], "local",
              deploy=[sys.executable, "deploy.py", "deploy", str(root / "site")],
              health=[sys.executable, "deploy.py", "health", str(root / "site")])
    return {"id": task_id, "project": str(project), "remote": str(remote),
            "note": "로컬 검사 완료. queue DEMO-RELEASE --action release로 실제 푸시·병합·배포를 실행하세요."}
