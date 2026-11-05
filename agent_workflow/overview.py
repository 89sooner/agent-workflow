"""Bounded, read-only project overview across workflow and native observation."""
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlencode

from .connections import project_key, task_project


def snapshot(ledgers, native):
    projects, items, errors = {}, [], list(native.get('errors', []))
    keys = {}

    def project(path):
        if path not in keys:
            keys[path] = project_key(path)
        key = keys[path]
        projects.setdefault(key, dict(id=key, path=path, managed=0, native=0))
        return key

    for source in ledgers:
        if source.get('kind') == 'opencode':
            continue  # Native snapshot already owns these sessions.
        if source['status'] != 'available':
            errors.append(dict(source=source['id'], error='원장을 조회할 수 없습니다.'))
            continue
        c = None
        pending = []
        try:
            db = Path(source['home']) / 'state.sqlite3'
            c = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
            for raw, in c.execute('SELECT data FROM tasks ORDER BY rowid DESC LIMIT 1001'):
                task = json.loads(raw)
                path = task_project(task)
                if not isinstance(path, str) or not isinstance(task.get('id'), str):
                    raise ValueError('잘못된 작업 식별')
                pending.append(dict(id='managed:'+source['id']+':'+task['id'], kind='managed',
                    source=source['id'], project_path=path, name=str(task.get('goal', task['id'])),
                    state=str(task.get('status', 'unknown')), native_id=task['id'],
                    href='/mission?'+urlencode(dict(ledger=source['id'], task=task['id']))))
            if len(pending) > 1000:
                errors.append(dict(source=source['id'], error='최근 작업 1000개만 표시합니다.'))
            for row in pending[:1000]:
                row['project_id'] = project(row.pop('project_path'))
                projects[row['project_id']]['managed'] += 1
                items.append(row)
        except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
            errors.append(dict(source=source['id'], error='원장 작업 데이터를 읽을 수 없습니다.'))
        finally:
            if c is not None:
                c.close()
    for row in native.get('sessions', []):
        key = project(row['project'])
        projects[key]['native'] += 1
        items.append(dict(id='native:'+row['id'], kind='native', source=row['source'],
            project_id=key, name=row['name'], state=row['state'], native_id=row['native_id'],
            provider=row['provider'], fresh=row['fresh'],
            href='/sessions?'+urlencode(dict(session=row['id']))))
    return dict(schema_version=1, projects=sorted(projects.values(), key=lambda p:p['path']),
                items=items, errors=errors, execution_started=False)
