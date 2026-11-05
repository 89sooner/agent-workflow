"""Read-only connection evidence. Never starts a host or writes a registry."""
import json
import sqlite3
from pathlib import Path

from . import native, opencode
from .connections import state_home
from .core import WorkflowError


def connection(home, project, provider):
    if provider not in native.PROVIDERS:
        raise WorkflowError('지원하지 않는 관측 도구입니다.')
    path = Path(project).expanduser()
    if not path.is_absolute():
        raise WorkflowError('프로젝트 절대 경로를 입력하세요.')
    project = str(path.resolve())
    steps = []

    def step(identifier, status, reason, action):
        steps.append(dict(id=identifier, status=status, reason=reason, action=action))

    exists = path.is_dir()
    step('project', 'pass' if exists else 'fail',
         '프로젝트 폴더 확인' if exists else '프로젝트 폴더가 없습니다.', '서버 컴퓨터의 프로젝트 경로를 확인하세요.')
    try:
        registrations = [s for s in native.sources(home)
                         if s.get('project') == project and s.get('provider', 'claude') == provider]
        if any(s.get('mode', 'direct') not in ('direct', 'plugin')
               or (s.get('command') is not None and not isinstance(s['command'], str))
               for s in registrations):
            raise WorkflowError('잘못된 등록 형식')
        api_sources = [s for s in opencode.sources(home) if s.get('directory') == project] if provider == 'opencode' else []
    except (WorkflowError, OSError, ValueError, AttributeError, TypeError):
        step('registration', 'fail', '연결 설정을 읽을 수 없습니다.', '연결 설정 파일을 확인하세요.')
        return dict(schema_version=1, project=project, provider=provider, mode=None, last_seen=None, steps=steps, execution_started=False)
    registered = bool(registrations or api_sources)
    mode = registrations[0].get('mode', 'direct') if registrations else 'api' if api_sources else None
    step('registration', 'pass' if registered else 'fail',
         '프로젝트가 등록되었습니다.' if registered else '등록된 연결이 없습니다.',
         '프로젝트 연결 또는 native install/register를 사용하세요.')
    if mode == 'direct' and provider in {'claude', 'codex'}:
        settings = path / ('.claude/settings.local.json' if provider == 'claude' else '.codex/hooks.json')
        try:
            data = json.loads(settings.read_text())
            expected = registrations[0].get('command') or native.command(home, project, provider)
            events = native.CODEX_EVENTS if provider == 'codex' else native.EVENT_STATES
            hooks = data.get('hooks', {})
            installed = all(any(h.get('command') == expected
                                for entry in hooks.get(event, []) for h in entry.get('hooks', []))
                            for event in events)
            step('installation', 'pass' if installed else 'fail',
                 '등록된 관측 훅 확인' if installed else '관측 훅 일부가 없거나 변경되었습니다.',
                 '기존 설정을 보존하고 native install로 복구하세요.')
        except (OSError, ValueError, AttributeError, TypeError):
            step('installation', 'fail', '훅 설정을 읽을 수 없습니다.', '설정 파일을 확인하세요.')
    else:
        step('installation', 'not-applicable' if mode == 'api' else 'unknown',
             'API 연결은 훅 설치가 필요하지 않습니다.' if mode == 'api' else '플러그인 로딩 여부는 로컬 등록만으로 확인할 수 없습니다.',
             '사용 도구의 플러그인 목록과 설정을 확인하세요.')
    step('host_activation', 'not-applicable' if mode == 'api' else 'unknown',
         '호스트 신뢰·설정 재로딩은 자동 확인하지 않습니다.',
         'Codex는 /hooks 검토, 다른 도구는 플러그인·훅 활성화와 새 세션을 확인하세요.')
    last_seen = None
    db = state_home(home) / 'native-observation.sqlite3'
    try:
        if db.exists():
            c = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)
            try:
                last_seen = c.execute('SELECT MAX(last_seen) FROM sessions WHERE project=? AND provider=?',
                                      (project, provider)).fetchone()[0]
            finally:
                c.close()
        step('first_event', 'pass' if last_seen is not None else 'unknown',
             '수신 이력이 있습니다. 현재 활성화의 보장은 아닙니다.' if last_seen is not None else '아직 수신 이력이 없습니다.',
             '같은 관측 home을 사용하도록 설정한 뒤 새 세션을 시작하세요.')
    except sqlite3.Error:
        step('first_event', 'unknown', '관측 기록을 읽을 수 없습니다.', '관측 DB 접근과 스키마를 확인하세요.')
    return dict(schema_version=1, project=project, provider=provider, mode=mode,
                last_seen=last_seen, steps=steps, execution_started=False)
