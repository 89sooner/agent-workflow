'use strict';
const $ = (id) => document.getElementById(id);
const state = { tasks: [], jobs: [], profiles: [], notifications: [], selected: null, detail: null, token: null, busy: false, polling: false, listKey: '', detailKey: '', jobsKey: '', profileKey: '', holdTaskId: null,
  project: '', view: 'observatory', projectKey: '', sidebarOpen: false, preferences: { threeD: true, motion: 'system' } };
try {
  state.selected = localStorage.getItem('agent-workflow-selected');
  state.project = localStorage.getItem('agent-workflow-project') || '';
  const view = localStorage.getItem('agent-workflow-view');
  if (['observatory', 'tasks', 'runs', 'settings'].includes(view)) state.view = view;
  const preferences = JSON.parse(localStorage.getItem('agent-workflow-preferences') || '{}');
  state.preferences.threeD = preferences?.threeD !== false;
  state.preferences.motion = preferences?.motion === 'reduce' ? 'reduce' : 'system';
} catch (_) { /* storage is optional */ }
const labels = { ready: '준비됨', running: '실행 중', implementing: '구현 중', checking: '검증 중', reviewing: '검토 중', needs_review: '검토 대기', ready_to_finish: '마감 대기', blocked: '확인 필요', completed: '로컬 완료', queued: '대기 중', succeeded: '완료', failed: '실패', cancelled: '취소됨', canceled: '취소됨', configured: '설정됨', merged: '병합 완료', deployed: '배포 완료', deploying: '배포 중', merging: '병합 중', pr_created: 'PR 생성됨', pushed: '푸시 완료', pending: '대기 중' };
Object.assign(labels, { interrupted: '중단됨', committed: '커밋 완료', pull_request: 'PR 생성됨', preparing: '통합 준비 중', created: '작업 등록', checks_started: '필수 검사 시작', checks_finished: '필수 검사 종료', checks_interrupted: '검사 중단', check_added: '검사 명령 추가', agent_started: '에이전트 실행 시작', implementation_returned: '구현 결과 수신', review_finished: '독립 검토 종료', checkpoint: '체크포인트 저장', assigned: '담당자 변경', release_configured: '통합 · 배포 설정', release_preparing: '통합 준비 시작', release_committed: '검증한 변경 커밋', release_pushed: '원격 브랜치 푸시', release_pull_request: 'PR 생성', release_merged: '브랜치 병합 완료', release_deploying: '배포 시작', release_deployed: '배포 완료', release_updated: '통합 · 배포 기록 갱신' });
const label = (value) => labels[value] || (String(value || '').startsWith('release_') ? `통합 · 배포: ${labels[value.slice(8)] || value.slice(8)}` : value) || '미확인';
const expandedEvents = new Set();
const expandedLogs = new Set();
Object.assign(labels, { agent_failed: '실행 오류 확인', recovery_started: '복구 시작', recovery_checked: '남은 코드 검사', profile_applied: '프로젝트 설정 적용', workspace_prepared: '작업 환경 준비', setup_started: '환경 준비 시작', setup_failed: '환경 준비 실패', service_started: '검사 서버 시작', service_stopped: '검사 서버 종료', documents_updated: '관리 문서 갱신', closure_finished: '결과와 인계 저장', release_cleared: '릴리스 설정 해제' });
Object.assign(labels, { held: '수동 보류', hold_set: '작업 보류', hold_cleared: '보류 해제', task_held: '작업 보류', task_unheld: '보류 해제' });
function eventSummary(event) {
  const data = event.data;
  if (!data || typeof data !== 'object') return textValue(data);
  if (data.error || data.reason) return textValue(data.error || data.reason);
  if (data.summary) return textValue(data.summary);
  if (event.kind === 'checks_finished') {
    const results = Array.isArray(data.results) ? data.results : [];
    const passed = results.filter((r) => r.exit_code === 0 && !r.timed_out).length;
    return `${data.passed ? '필수 검사 통과' : '필수 검사 실패'}${results.length ? ` · ${passed}/${results.length}개 통과` : ''}${data.code_changed_during_checks ? ' · 검사 중 코드 변경 감지' : ''}`;
  }
  if (data.pr) return `PR #${data.pr}`;
  const sha = data.deployed_revision || data.merge_commit || data.commit;
  if (sha) return `대상 커밋 ${String(sha).slice(0, 10)}`;
  if (data.deployment_result) return `배포 명령 종료 · exit ${data.deployment_result.exit_code ?? '미기록'}`;
  if (data.health_result) return `상태 확인 명령 종료 · exit ${data.health_result.exit_code ?? '미기록'}`;
  if (event.kind === 'release_configured') return `${data.branch || '작업 브랜치'} → ${data.base || '기본 브랜치'} · ${data.deploy ? '배포 포함' : '통합까지'}`;
  if (data.outcome) return `결과: ${label(data.outcome)}`;
  if (data.role || data.agent) return [data.agent, data.role === 'review' ? '독립 검토' : data.role === 'implement' ? '구현' : data.role].filter(Boolean).join(' · ');
  return '';
}
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = String(text);
  if (className) el.className = className;
  return el;
};
const iconPaths = {
  check: ['M5 12l4 4L19 6'],
  code: ['m8 5-6 7 6 7', 'm16 5 6 7-6 7', 'm14 3-4 18'],
  shield: ['M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6Z', 'm8 12 3 3 5-6'],
  review: ['M3 12s3-7 9-7 9 7 9 7-3 7-9 7-9-7-9-7Z', 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6'],
  branch: ['M6 3v11a5 5 0 0 0 10 0V5', 'm12 8 4-4 4 4', 'M3 3h6'],
  rocket: ['M12 15 9 12s1-7 11-8c-1 10-8 11-8 11Z', 'm9 9-5 1-2 5 7-1', 'm15 15-1 5-5 2 1-7', 'm5 19-2 2'],
  play: ['m8 4 12 8-12 8Z'],
  folder: ['M3 6h7l2 3h9v11H3Z'],
  arrow: ['M5 12h14', 'm14 7 5 5-5 5'],
  clock: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18', 'M12 7v5l3 2'],
  alert: ['M12 3 2 21h20Z', 'M12 9v5', 'M12 17v.1'],
  circle: ['M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18'],
  minus: ['M6 12h12']
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  for (const d of iconPaths[name] || iconPaths.circle) {
    const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', d); svg.append(path);
  }
  return svg;
}
function badge(value) {
  const kind = ['completed', 'succeeded', 'merged', 'deployed'].includes(value) ? 'good'
    : ['blocked', 'failed', 'interrupted'].includes(value) ? 'bad'
    : ['running', 'implementing', 'checking', 'reviewing', 'queued', 'deploying', 'merging', 'held'].includes(value) ? 'warn' : '';
  return node('span', label(value), `badge ${kind}`);
}
function date(value) {
  if (!value) return '시간 미기록';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('ko-KR', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false
  });
}
function textValue(value) { return value == null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
function notice(id, message) { const el = $(id); el.textContent = message || ''; el.hidden = !message; }
function projectName(path) { return String(path || '').split(/[\\/]/).filter(Boolean).pop() || '프로젝트'; }
function releasePending(task) {
  const r = task.release;
  return Boolean(r && r.stage !== 'deployed' && !(r.stage === 'merged' && !r.config?.deploy && r.integration_verification?.passed && r.integration_verification.revision === r.merge_commit));
}
function needsAttention(task) { return task.status === 'blocked' || Boolean(task.hold || task.release?.error); }
async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  let body;
  try { body = await response.json(); } catch (_) { throw new Error(`서버 응답을 읽지 못했습니다 (${response.status}).`); }
  if (!response.ok) throw new Error(textValue(body.error || body.message) || `요청 실패 (${response.status})`);
  return body;
}
function selectedTask() { return state.tasks.find((task) => task.id === state.selected); }
function projectSource(task) {
  return [task.workspace?.source, task.project_config?.project, task.project].find(value => typeof value === 'string' && value.length) || '';
}
function scopedTasks() { return state.tasks.filter(task => !state.project || projectSource(task) === state.project); }
function scopedJobs() {
  if (!state.project) return state.jobs;
  const ids = new Set(scopedTasks().map(task => task.id));
  return state.jobs.filter(job => ids.has(job.task_id));
}
function renderProjects() {
  const paths = [...new Set([...state.tasks.map(projectSource), ...state.profiles.map(profile => profile.config?.project)].filter(value => typeof value === 'string' && value.length))].sort();
  if (state.project && !paths.includes(state.project)) state.project = '';
  const key = JSON.stringify([paths, state.project]);
  if (key !== state.projectKey) {
    state.projectKey = key;
    const all = node('option', '모든 프로젝트'); all.value = '';
    const options = paths.map(path => {
      const duplicateName = paths.filter(other => projectName(other) === projectName(path)).length > 1;
      const option = node('option', duplicateName ? path : projectName(path)); option.value = path; return option;
    });
    $('workspace-project').replaceChildren(all, ...options);
    $('workspace-project').value = state.project;
  }
  $('workspace-name').textContent = state.project ? projectName(state.project) : '모든 프로젝트';
  $('workspace-name').title = state.project || '연결된 모든 로컬 프로젝트';
  $('settings-projects').textContent = String(paths.length);
  $('task-count').textContent = String(scopedTasks().length);
}
function renderOverview() {
  const tasks = scopedTasks(), jobs = scopedJobs();
  const running = jobs.filter(job => job.status === 'running').length;
  const queued = jobs.filter(job => job.status === 'queued').length;
  $('metric-running').textContent = String(running);
  $('metric-queued').textContent = String(queued);
  $('metric-running').parentElement.title = '실행 중인 큐 작업 수입니다. 별도 CLI 호출의 활동은 관제실에서 확인합니다.';
  $('metric-queued').parentElement.title = '대기 중인 큐 작업 수입니다. 보류 중인 작업도 포함합니다.';
  $('metric-active').textContent = String(running + queued);
  $('metric-blocked').textContent = String(tasks.filter(needsAttention).length);
  $('metric-completed').textContent = String(tasks.filter(task => task.status === 'completed').length);
  $('metric-deployed').textContent = String(tasks.filter((task) => task.release?.stage === 'deployed').length);
  $('nav-running-count').textContent = String(running + queued);
  $('nav-running-count').hidden = running + queued === 0;
}
function renderList() {
  const query = $('search').value.trim().toLocaleLowerCase();
  const filter = $('filter').value;
  const tasks = scopedTasks().filter((task) => [task.id, task.goal, task.project, projectSource(task)].join(' ').toLocaleLowerCase().includes(query))
    .filter((task) => filter === 'all' || (filter === 'completed' ? task.status === 'completed'
      : filter === 'blocked' ? needsAttention(task)
        : !needsAttention(task) && (task.status !== 'completed' || releasePending(task))));
  const key = JSON.stringify([tasks.map((task) => [task.id, task.goal, task.status, task.project, task.release?.stage, task.hold]), state.selected, state.project, query, filter]);
  if (key === state.listKey) return;
  state.listKey = key;
  const fragment = document.createDocumentFragment();
  for (const task of tasks) {
    const button = node('button', null, `task-item${task.id === state.selected ? ' selected' : ''}`);
    button.type = 'button'; button.setAttribute('aria-current', String(task.id === state.selected));
    button.append(node('span', task.id, 'task-id'), node('span', task.goal, 'task-goal'));
    const footer = node('div', null, 'task-item-footer');
    const project = node('span', projectName(projectSource(task)), 'task-project'); project.title = projectSource(task);
    footer.append(badge(task.hold ? 'held' : task.release?.stage === 'deployed' ? 'deployed' : task.status), project); button.append(footer);
    button.addEventListener('click', () => selectTask(task.id)); fragment.append(button);
  }
  if (!tasks.length) fragment.append(node('p', state.tasks.length ? '조건에 맞는 작업이 없습니다.' : '아직 등록된 작업이 없습니다.', 'empty-inline'));
  replaceKeepingFocus($('task-list'), fragment);
}
function replaceKeepingFocus(container, content) {
  const active = document.activeElement;
  const focusId = container.contains(active) ? active.dataset.focus : null;
  const taskId = container.contains(active) && active.classList.contains('task-item') ? active.querySelector('.task-id')?.textContent : null;
  container.replaceChildren(content);
  if (focusId) [...container.querySelectorAll('[data-focus]')].find((el) => el.dataset.focus === focusId)?.focus({ preventScroll: true });
  else if (taskId) [...container.querySelectorAll('.task-item')].find((el) => el.querySelector('.task-id')?.textContent === taskId)?.focus({ preventScroll: true });
}
function selectTask(id) {
  const returnToContent = state.sidebarOpen;
  state.selected = id; state.detail = null; state.detailKey = '';
  if (!['observatory', 'tasks'].includes(state.view)) showView('observatory', false);
  closeSidebar(false);
  try { localStorage.setItem('agent-workflow-selected', id); } catch (_) { /* optional persistence */ }
  renderList(); renderDetail(); loadDetail(id);
  if (returnToContent) { window.scrollTo({ top: 0, behavior: 'instant' }); $('page-title').focus({ preventScroll: true }); }
}
function sectionTitle(kicker, title) {
  const wrapper = node('div', null, 'section-heading'); const inner = node('div');
  inner.append(node('span', kicker, 'section-kicker'), node('h2', title)); wrapper.append(inner); return wrapper;
}
function workflowTrack(task, detail) {
  const r = task.release, v = task.verification, review = task.review, fresh = detail?.freshness;
  const completed = task.status === 'completed';
  const steps = [
    { title: '구현', icon: 'code', done: completed || task.implementation?.outcome === 'ready', active: task.status === 'implementing', error: task.status === 'blocked' && !v && !review },
    { title: '검증', icon: 'shield', done: Boolean(v?.passed && fresh?.verification_is_current), active: task.status === 'checking', error: Boolean(v && (!v.passed || fresh?.verification_is_current === false)) },
    { title: '독립 리뷰', icon: 'review', skip: task.reviewer === 'none' ? '생략' : null, done: Boolean(review?.verdict === 'pass' && fresh?.review_is_current), active: task.status === 'reviewing', error: Boolean(review && (review.verdict !== 'pass' || fresh?.review_is_current === false)) },
    { title: '병합', icon: 'branch', skip: r ? null : '미설정', done: Boolean(r?.merge_commit), active: Boolean(r && ['preparing', 'committed', 'pushed', 'pull_request'].includes(r.stage)), error: Boolean(r?.error && !r.merge_commit) },
    { title: '배포', icon: 'rocket', skip: r?.config?.deploy ? null : '미설정', done: Boolean(r?.stage === 'deployed' && r.deployed_revision && r.health_result?.exit_code === 0), active: r?.stage === 'deploying', error: Boolean(r?.error && r.merge_commit) }
  ];
  const track = node('ol', null, 'workflow-track'); track.setAttribute('aria-label', '작업 단계');
  for (const step of steps) {
    const kind = step.skip ? 'skipped' : step.error ? 'attention' : step.done ? 'done' : step.active ? 'current' : 'pending';
    const item = node('li', null, `workflow-step ${kind}`);
    if (kind === 'current') item.setAttribute('aria-current', 'step');
    const mark = node('span', null, 'step-icon'); mark.append(icon(kind === 'done' ? 'check' : step.skip ? 'minus' : kind === 'attention' ? 'alert' : step.icon));
    const description = step.skip || (kind === 'done' ? '완료' : kind === 'attention' ? '확인 필요' : kind === 'current' ? '진행 중' : '대기');
    item.append(mark, node('span', step.title, 'step-label'), node('span', description, 'step-state')); track.append(item);
  }
  return track;
}
function agentMeta(name, role, isReviewer = false) {
  const group = node('span', null, 'agent-meta');
  const avatar = node('span', name && name !== 'none' ? name.charAt(0).toUpperCase() : '—', `agent-avatar${isReviewer ? ' reviewer' : ''}`);
  avatar.setAttribute('aria-hidden', 'true');
  group.append(avatar, node('span', role), node('span', name === 'none' ? '미지정' : name || '미지정', 'agent-name')); return group;
}
function actionButton(title, glyph, key, disabled, callback, primary = false) {
  const button = node('button', null, `button${primary ? ' primary' : ''}`);
  button.type = 'button'; button.dataset.focus = key; button.disabled = disabled;
  button.append(icon(glyph), node('span', title)); button.addEventListener('click', callback); return button;
}
function renderDetail() {
  const detail = state.detail, task = detail?.task || selectedTask();
  syncObservatoryVisibility();
  window.WorkflowObservatory?.update({ task, detail, jobs: state.jobs });
  const pending = state.jobs.some((job) => job.task_id === state.selected && ['queued', 'running'].includes(job.status));
  const key = JSON.stringify([task, detail?.observed_git, detail?.freshness, detail?.events, state.busy, pending, Boolean(detail), state.view]);
  if (key === state.detailKey) return;
  state.detailKey = key;
  const fragment = document.createDocumentFragment(), contextFragment = document.createDocumentFragment();
  if (!task) {
    const empty = node('div', null, 'empty'); const mark = node('div', null, 'empty-symbol'); mark.append(icon('branch'));
    empty.append(mark, node('h2', '첫 워크플로를 시작해 보세요.'), node('p', 'Codex·Claude에서 agent-workflow 스킬로 작업을 등록하면, 실행과 검증 과정을 이곳에서 확인할 수 있습니다.'), node('code', 'agent-workflow create --help'));
    contextFragment.append(empty); replaceKeepingFocus($('task-context'), contextFragment); replaceKeepingFocus($('detail'), fragment); return;
  }
  const release = task.release, current = detail?.freshness?.completion_is_current, held = Boolean(task.hold);
  const observationError = detail?.observed_git?.error;
  const head = node('div', null, 'detail-header'), eyebrow = node('div', null, 'eyebrow');
  eyebrow.append(node('span', task.id, 'task-id'), badge(task.status));
  if (held) eyebrow.append(badge('held'));
  if (release?.stage === 'deployed') eyebrow.append(badge('deployed'));
  const path = node('div', null, 'project-path'); path.append(icon('folder'), node('span', task.project));
  head.append(eyebrow, node('h2', task.goal, 'detail-title'), path);
  const meta = node('div', null, 'task-meta');
  meta.append(agentMeta(task.owner, '구현'), agentMeta(task.reviewer, '리뷰', true));
  const stale = task.status === 'completed' && current === false;
  const evidenceText = observationError ? '현재 상태 관찰 불가' : stale ? '코드·입력 또는 보호 경로 변경 · 재검증 필요' : task.status === 'completed' && current === true ? '현재 코드의 완료 증거 확인됨' : task.status === 'completed' ? (detail ? '현재 완료 증거를 판정할 수 없습니다' : '완료 증거 확인 중') : '실제 검사 결과를 기준으로 진행';
  const evidence = node('span', null, `evidence-note${observationError ? ' unobservable' : stale ? ' stale' : current ? '' : ' neutral'}`); evidence.append(icon(observationError || stale ? 'alert' : current ? 'shield' : 'clock'), node('span', evidenceText)); meta.append(evidence);
  if (release?.merge_commit) {
    const receipt = release.integration_verification;
    const verified = receipt?.passed && receipt.revision === release.merge_commit;
    meta.append(node('span', verified ? '통합 검증 통과' : receipt?.passed === false ? '통합 검증 실패' : '통합 검증 미확인', `badge ${verified ? 'good' : receipt?.passed === false ? 'bad' : 'warn'}`));
  }
  head.append(meta); contextFragment.append(head);
  if (observationError) contextFragment.append(node('div', `작업 폴더 상태를 확인하지 못했습니다: ${textValue(observationError)} 저장된 완료 이력은 유지되며, 작업 폴더를 복구한 뒤 새로고침하면 최신 증거를 다시 확인합니다.`, 'notice evidence-error'));
  if (held) {
    const hold = node('div', null, 'notice hold-notice');
    hold.append(node('span', `수동 보류 · ${task.hold.reason || '보류 사유 미기록'}`, 'hold-reason'), node('span', '기존 대기 작업과 다음 단계 시작을 보류합니다. 실행 중인 프로세스는 현재 단계 종료 후 멈춥니다. 해제하면 기존 대기 작업은 다시 실행될 수 있습니다.', 'hold-detail')); contextFragment.append(hold);
  }
  if (task.blocker || release?.error) contextFragment.append(node('div', textValue(task.blocker || release.error), 'notice error'));
  fragment.append(workflowTrack(task, detail));
  const actions = node('div', null, 'detail-actions');
  const localDone = task.status === 'completed';
  actions.append(actionButton(held ? '수동 보류 중' : pending ? '실행 대기 · 진행 중' : localDone ? '로컬 작업 완료' : '워크플로 실행', localDone ? 'check' : 'play', 'pipeline', state.busy || pending || localDone || held, () => queue(task.id, 'pipeline'), true));
  if (release) actions.append(actionButton(release.stage === 'deployed' ? '배포 완료' : '통합 · 배포 실행', 'rocket', 'release', state.busy || pending || held || !releasePending(task), () => queue(task.id, 'release')));
  actions.append(actionButton(held ? '보류 해제' : '작업 보류', held ? 'play' : 'minus', 'hold-toggle', state.busy, () => held ? mutate('/api/unhold', { task_id: task.id }, '수동 보류를 해제했습니다. 기존 대기 작업은 다시 실행될 수 있습니다.') : openHold(task)));
  if (release?.stage === 'configured' && !release.commit) actions.append(actionButton('릴리스 설정 해제', 'minus', 'release-clear', state.busy || pending, () => mutate('/api/release-clear', { task_id: task.id }, '릴리스 설정을 해제했습니다. 이후 실행은 로컬 완료까지 진행합니다.')));
  const hint = held ? '다음 단계를 시작하려면 보류 사유를 확인하고 해제하세요.' : pending ? '실행 대기열에서 진행 상태를 확인하세요.' : release?.stage === 'deployed' ? '지정한 버전의 배포와 상태 확인을 마쳤습니다.' : release?.stage === 'merged' && releasePending(task) ? '병합 결과의 통합 검증과 남은 릴리스 단계를 이어서 실행합니다.' : localDone && releasePending(task) ? '통합 · 배포를 이어서 실행할 수 있습니다.' : localDone ? '다음 작업을 등록하거나 릴리스를 설정하세요.' : '등록된 범위와 검증 조건으로 실행합니다.';
  actions.append(actionButton(state.view === 'tasks' ? '관제실 보기' : '작업 상세', 'folder', 'task-view', false, () => showView(state.view === 'tasks' ? 'observatory' : 'tasks')));
  actions.append(node('span', hint, 'action-hint')); contextFragment.append(actions);
  replaceKeepingFocus($('task-context'), contextFragment);
  if (!detail) fragment.append(node('p', '상세 기록을 불러오는 중입니다…', 'muted'));
  const grid = node('div', null, 'detail-grid'), context = node('section', null, 'context-panel');
  context.append(sectionTitle('CONTINUITY', '마지막 작업과 다음 행동'));
  const checkpoint = node('div', null, 'checkpoint');
  checkpoint.append(node('h3', '최근 체크포인트'), node('p', task.checkpoint?.summary || '아직 체크포인트가 없습니다.'));
  const next = node('div', null, 'next-action'), nextLabel = node('span', null, 'next-label'); nextLabel.append(icon('arrow'), node('span', '다음 행동'));
  next.append(nextLabel, node('p', task.checkpoint?.next || '다음 행동이 기록되지 않았습니다.')); checkpoint.append(next); context.append(checkpoint);
  if (task.profile || task.execution || task.usage) {
    const policy = node('p', null, 'runtime-policy');
    const parts = [];
    if (task.profile) parts.push(`프로젝트 설정: ${task.profile.name}`);
    if (task.execution?.models?.implement) parts.push(`구현 모델: ${task.execution.models.implement}`);
    if (task.usage) parts.push(`에이전트 호출 ${task.usage.agent_calls || 0}/${task.execution?.max_agent_calls || 12}회`);
    if (task.recoveries?.length) parts.push(`복구 ${task.recoveries.length}회`);
    policy.textContent = parts.join(' · '); context.append(policy);
  }
  if (task.criteria?.length) {
    context.append(node('h3', '완료 조건')); const list = node('ul', null, 'criteria');
    for (const criterion of task.criteria) { const item = node('li'); item.append(icon('circle'), node('span', textValue(criterion))); list.append(item); }
    context.append(list);
  }
  const activity = node('section', null, 'activity-panel'); activity.append(sectionTitle('ACTIVITY', '최근 실행 기록'));
  const timeline = node('ol', null, 'timeline'), events = Array.isArray(detail?.events) ? detail.events.slice(0, 6) : [];
  for (const event of events) {
    const item = node('li'), top = node('div', null, 'event-top'), time = node('time', date(event.at));
    if (event.at) time.dateTime = event.at;
    top.append(node('span', label(event.kind)), time); item.append(top);
    if (event.data) {
      const summary = eventSummary(event);
      if (summary) item.append(node('p', summary.length > 220 ? `${summary.slice(0, 220)}…` : summary, 'event-detail'));
      const raw = textValue(event.data);
      if ((typeof event.data === 'object' && Object.keys(event.data).length) || raw.length > 220) {
        const more = node('details', null, 'event-raw'), eventKey = `${task.id}:${event.at}:${event.kind}`;
        more.open = expandedEvents.has(eventKey);
        const summaryEl = node('summary', '세부 기록'); summaryEl.dataset.focus = eventKey;
        more.append(summaryEl, node('pre', raw));
        more.addEventListener('toggle', () => { if (more.open) expandedEvents.add(eventKey); else expandedEvents.delete(eventKey); }); item.append(more);
      }
    }
    timeline.append(item);
  }
  if (!events.length) activity.append(node('p', detail ? '아직 실행 기록이 없습니다.' : '기록을 확인하고 있습니다.', 'empty-inline'));
  else activity.append(timeline);
  grid.append(context, activity); fragment.append(grid); replaceKeepingFocus($('detail'), fragment);
}
function renderJobs() {
  const jobs = [...scopedJobs()].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
  const key = JSON.stringify([jobs, state.busy, state.project, state.tasks.map(task => [task.id, task.hold])]); if (key === state.jobsKey) return; state.jobsKey = key;
  $('queue-count').textContent = String(jobs.filter((job) => ['queued', 'running'].includes(job.status)).length);
  const fragment = document.createDocumentFragment();
  for (const job of jobs.slice(0, 25)) {
    const row = node('div', null, 'job'), glyph = node('div', null, 'job-icon'), start = node('div'); glyph.append(icon(job.action === 'release' ? 'rocket' : 'code'));
    start.append(node('div', `${job.task_id} · ${job.action === 'release' ? '통합 · 배포' : '구현 · 검증'}`, 'job-title'));
    const meta = node('div', null, 'job-meta'); meta.append(node('span', date(job.created_at)), node('span', String(job.id).slice(0, 8))); start.append(meta);
    if (job.error) start.append(node('div', textValue(job.error), 'job-error'));
    if (job.log_path) {
      const log = node('details', null, 'event-raw'); log.open = expandedLogs.has(job.id);
      const summary = node('summary', '실행 로그'); summary.dataset.focus = `log-${job.id}`;
      const text = node('pre', '로그를 열어 확인하세요.');
      const reload = node('button', '로그 새로고침', 'button small'); reload.type = 'button';
      const load = async () => {
        reload.disabled = true;
        try { const result = await request(`/api/job-log/${encodeURIComponent(job.id)}`); text.textContent = (result.truncated ? '[최근 64 KiB]\n' : '') + result.text; }
        catch (error) { text.textContent = error.message; }
        finally { reload.disabled = false; }
      };
      log.append(summary, text, reload);
      log.addEventListener('toggle', () => { if (log.open) { expandedLogs.add(job.id); load(); } else expandedLogs.delete(job.id); });
      reload.addEventListener('click', load); start.append(log);
    }
    const end = node('div', null, 'job-end'); end.append(badge(job.status));
    const held = Boolean(state.tasks.find(task => task.id === job.task_id)?.hold);
    if (held && job.status === 'queued') end.append(badge('held'));
    if (job.status === 'queued') {
      const button = node('button', '대기 취소', 'button small'); button.type = 'button'; button.dataset.focus = `cancel-${job.id}`; button.disabled = state.busy;
      button.setAttribute('aria-label', `${job.task_id} 실행 대기 취소`); button.addEventListener('click', () => mutate('/api/cancel', { job_id: job.id }, '실행 대기를 취소했습니다.')); end.append(button);
    }
    if (job.status === 'running') {
      const button = node('button', job.cancel_requested ? '중단 요청됨' : '실행 중단', 'button small');
      button.type = 'button'; button.dataset.focus = `stop-${job.id}`; button.disabled = state.busy || Boolean(job.cancel_requested);
      button.setAttribute('aria-label', `${job.task_id} 실행 중단`);
      button.addEventListener('click', () => mutate('/api/stop', { job_id: job.id }, '이 실행에 중단을 요청했습니다. 파일 변경은 보존합니다.'));
      end.append(button);
    }
    if (['failed', 'interrupted', 'cancelled'].includes(job.status)) {
      const active = state.jobs.some(other => other.task_id === job.task_id && ['queued', 'running'].includes(other.status));
      const button = node('button', '재개', 'button small'); button.type = 'button'; button.dataset.focus = `resume-${job.id}`;
      button.disabled = state.busy || active || held; button.setAttribute('aria-label', `${job.task_id} 실행 재개`);
      button.addEventListener('click', () => mutate('/api/resume', { job_id: job.id }, '같은 작업을 이전 실행 설정으로 대기열에 등록했습니다.')); end.append(button);
    }
    row.append(glyph, start, end); fragment.append(row);
  }
  if (!jobs.length) fragment.append(node('p', '아직 실행 기록이 없습니다. 작업을 선택하고 첫 실행을 시작하세요.', 'empty-inline'));
  replaceKeepingFocus($('jobs'), fragment);
}
async function queue(taskId, action) {
  if (state.tasks.find(task => task.id === taskId)?.hold) { notice('error', '작업이 수동 보류 중입니다. 보류 사유를 확인하고 해제한 뒤 실행하세요.'); return; }
  const payload = { task_id: taskId, action };
  for (const name of ['timeout', 'repairs', 'recoveries']) {
    const input = $(`option-${name}`);
    if (input.value !== '') {
      if (!input.checkValidity()) {
        showView('tasks', false);
        input.closest('details').open = true;
        notice('error', '실행 옵션의 허용 범위를 확인해 주세요. 값을 수정한 뒤 다시 실행할 수 있습니다.');
        input.focus(); input.scrollIntoView({ block: 'center', behavior: 'instant' }); input.reportValidity();
        return;
      }
      payload[name] = Number(input.value);
    }
  }
  await mutate('/api/queue', payload, action === 'release' ? '통합 · 배포 실행을 대기열에 추가했습니다.' : '작업 실행을 대기열에 추가했습니다.');
}
async function mutate(path, payload, success) {
  if (state.busy) return false;
  state.busy = true; notice('action-message', '요청을 처리하고 있습니다…'); renderDetail(); renderJobs();
  try {
    if (!state.token) throw new Error('서버 연결 후 다시 시도하세요.');
    const result = await request(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workflow-Token': state.token }, body: JSON.stringify(payload) });
    if (result.task) {
      state.tasks = state.tasks.map(task => task.id === result.task.id ? result.task : task);
      if (state.detail?.task?.id === result.task.id) state.detail.task = result.task;
      renderOverview(); renderList();
    }
    notice('action-message', success); await refresh(); window.WorkflowObservatory?.refresh(); return true;
  } catch (error) { notice('action-message', ''); notice('error', error.message); return false; }
  finally { state.busy = false; renderDetail(); renderJobs(); }
}
async function loadDetail(id) {
  if (!id) return;
  try { const result = await request(`/api/tasks/${encodeURIComponent(id)}`); if (id !== state.selected) return; state.detail = result; renderDetail(); }
  catch (error) { if (id === state.selected) notice('error', `상세 기록: ${error.message}`); }
}
async function refresh() {
  if (state.polling) return;
  state.polling = true; $('refresh').disabled = true;
  try {
    const result = await request('/api/state');
    state.tasks = Array.isArray(result.tasks) ? result.tasks : []; state.jobs = Array.isArray(result.jobs) ? result.jobs : []; state.token = result.csrf_token;
    state.profiles = Array.isArray(result.profiles) ? result.profiles : [];
    state.notifications = Array.isArray(result.notifications) ? result.notifications : [];
    renderProfiles(); renderProjects(); renderNotifications(); renderSettings(result);
    $('version').textContent = result.version ? `v${result.version}` : '';
    $('daemon').textContent = result.daemon?.running ? '실행 서비스 가동 중' : '실행 서비스 중지됨';
    $('connection-dot').className = 'dot online'; $('connection-label').textContent = '로컬 연결됨';
    $('last-updated').textContent = `최근 동기화 ${new Date().toLocaleTimeString('ko-KR', { hour12: false })}`; notice('error', '');
    if (!scopedTasks().some((task) => task.id === state.selected)) { state.selected = scopedTasks()[0]?.id || null; state.detail = null; state.detailKey = ''; }
    renderOverview(); renderList(); renderDetail(); renderJobs(); await loadDetail(state.selected);
  } catch (error) { $('connection-dot').className = 'dot offline'; $('connection-label').textContent = '연결 확인 필요'; $('settings-connection').textContent = '연결 확인 필요 · 마지막 기록 유지'; notice('error', `${error.message} 자동으로 다시 연결합니다.`); }
  finally { state.polling = false; $('refresh').disabled = false; $('loading').hidden = true; }
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('preferences-theme').value = theme;
  const title = theme === 'dark' ? '밝은 테마로 전환' : '어두운 테마로 전환';
  $('theme-toggle').setAttribute('aria-label', title); $('theme-toggle').title = title;
}
try { applyTheme(localStorage.getItem('agent-workflow-theme') === 'dark' ? 'dark' : 'light'); } catch (_) { applyTheme('light'); }
$('theme-toggle').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; applyTheme(theme);
  try { localStorage.setItem('agent-workflow-theme', theme); } catch (_) { /* optional persistence */ }
});
function profileHint() {
  const profile = state.profiles.find(item => item.name === $('create-profile').value);
  $('profile-hint').textContent = profile ? `${profile.config.project} · 구현 ${profile.config.owner} / 리뷰 ${profile.config.reviewer}` : 'CLI의 profile save로 프로젝트 설정을 먼저 등록하세요.';
}
function renderProfiles() {
  const key = JSON.stringify(state.profiles);
  if (key === state.profileKey) return;
  state.profileKey = key;
  const previous = $('create-profile').value;
  const options = state.profiles.map(profile => { const option = node('option', profile.name); option.value = profile.name; return option; });
  if (!options.length) { const option = node('option', '등록된 프로젝트 설정 없음'); option.value = ''; options.push(option); }
  $('create-profile').replaceChildren(...options);
  if (state.profiles.some(item => item.name === previous)) $('create-profile').value = previous;
  $('create-submit').disabled = !state.profiles.length;
  profileHint();
}
function renderNotifications() {
  const ids = new Set(scopedTasks().map(task => task.id));
  const notifications = state.notifications.filter(item => !state.project || ids.has(item.payload?.task_id));
  $('notification-section').hidden = !notifications.length;
  const fragment = document.createDocumentFragment();
  for (const item of notifications.slice(0, 6)) {
    const row = node('div', null, 'notification-row'), payload = item.payload || {};
    row.append(node('strong', payload.task_id), node('span', payload.blocker || payload.release_error || label(payload.job?.status || payload.status)), node('time', date(item.at)));
    if (item.delivery === 'failed' || item.delivery === 'started') row.append(node('span', '외부 알림 전달 확인 필요', 'badge warn'));
    fragment.append(row);
  }
  $('notifications').replaceChildren(fragment);
}
$('new-task').addEventListener('click', () => {
  const profile = state.profiles.find(item => item.name === $('create-profile').value);
  $('create-isolate').checked = profile?.config?.workspace?.isolate !== false;
  closeSidebar(false); notice('create-error', ''); $('create-dialog').showModal();
});
$('create-close').addEventListener('click', () => $('create-dialog').close());
$('create-profile').addEventListener('change', () => {
  profileHint();
  $('create-isolate').checked = state.profiles.find(item => item.name === $('create-profile').value)?.config?.workspace?.isolate !== false;
});
$('create-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.busy) return;
  state.busy = true; $('create-submit').disabled = true; notice('create-error', '');
  try {
    if (!state.token) throw new Error('서버 연결 후 다시 시도하세요.');
    const payload = { id: $('create-id').value.trim(), profile: $('create-profile').value,
      goal: $('create-goal').value.trim(), criteria: $('create-criteria').value.split('\n').map(line => line.trim()).filter(Boolean), isolate: $('create-isolate').checked };
    const result = await request('/api/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workflow-Token': state.token }, body: JSON.stringify(payload) });
    state.selected = result.task.id; state.detail = null; state.detailKey = '';
    if (state.project && projectSource(result.task) !== state.project) state.project = projectSource(result.task);
    showView('observatory', false); closeSidebar(false);
    $('create-dialog').close(); $('create-form').reset(); await refresh();
    notice('action-message', '작업을 등록했습니다. 범위와 검사 조건을 확인하고 실행하세요.');
  } catch (error) { notice('create-error', error.message); }
  finally { state.busy = false; $('create-submit').disabled = !state.profiles.length; renderDetail(); renderJobs(); }
});
function openHold(task) {
  state.holdTaskId = task.id; $('hold-task-id').textContent = task.id; $('hold-reason').value = '';
  notice('hold-error', ''); $('hold-dialog').showModal(); $('hold-reason').focus();
}
$('hold-close').addEventListener('click', () => $('hold-dialog').close());
$('hold-form').addEventListener('submit', async event => {
  event.preventDefault(); if (state.busy || !state.holdTaskId) return;
  const reason = $('hold-reason').value.trim();
  if (!reason) { notice('hold-error', '보류 사유를 입력하세요.'); return; }
  $('hold-submit').disabled = true; notice('hold-error', '');
  try {
    const success = await mutate('/api/hold', { task_id: state.holdTaskId, reason }, '작업을 수동 보류했습니다. 실행 중인 프로세스는 현재 단계까지 마칩니다.');
    if (success) $('hold-dialog').close();
    else notice('hold-error', $('error').textContent || '보류 요청을 처리하지 못했습니다.');
  } finally { $('hold-submit').disabled = false; }
});
const narrowNavigation = window.matchMedia('(max-width: 900px)');
const viewLabels = {
  observatory: ['에이전트 관제실', '누가 무엇을 하고 있는지, 작업의 흐름을 한눈에.'],
  tasks: ['작업', '선택한 작업의 목표, 실행 조건과 검증 근거를 확인하세요.'],
  runs: ['실행 기록', '선택한 프로젝트의 대기 작업과 실행 결과를 관리하세요.'],
  settings: ['워크스페이스 설정', '실행 환경을 확인하고, 나에게 맞는 작업 공간을 설정하세요.']
};
function syncObservatoryVisibility() {
  window.WorkflowObservatory?.setVisible(state.view === 'observatory' && !state.sidebarOpen);
}
function showView(view, focusHeading = true) {
  if (!Object.hasOwn(viewLabels, view)) return;
  state.view = view; document.body.dataset.view = view;
  for (const button of document.querySelectorAll('.workspace-navigation [data-view]')) {
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  $('page-title').textContent = viewLabels[view][0]; $('page-description').textContent = viewLabels[view][1];
  document.title = `${viewLabels[view][0]} · Agent Workflow`;
  $('task-context').hidden = !['observatory', 'tasks'].includes(view);
  $('task-panels').hidden = view !== 'tasks';
  $('execution-panel').hidden = !['observatory', 'runs', 'tasks'].includes(view);
  $('settings-panel').hidden = view !== 'settings';
  $('workspace-overview').hidden = view === 'settings';
  closeSidebar(false); syncObservatoryVisibility(); renderDetail();
  try { localStorage.setItem('agent-workflow-view', view); } catch (_) { /* optional persistence */ }
  if (focusHeading) { window.scrollTo({ top: 0, behavior: 'instant' }); $('page-title').focus({ preventScroll: true }); }
}
function setSidebar(open) {
  state.sidebarOpen = Boolean(open && narrowNavigation.matches);
  document.body.dataset.navigationOpen = String(state.sidebarOpen);
  $('navigation').inert = narrowNavigation.matches && !state.sidebarOpen;
  document.querySelector('.workspace-main').inert = state.sidebarOpen;
  $('navigation-backdrop').hidden = !state.sidebarOpen;
  $('navigation-toggle').setAttribute('aria-expanded', String(state.sidebarOpen));
  if (state.sidebarOpen) {
    $('navigation').setAttribute('role', 'dialog'); $('navigation').setAttribute('aria-modal', 'true');
  } else { $('navigation').removeAttribute('role'); $('navigation').removeAttribute('aria-modal'); }
  syncObservatoryVisibility();
}
function closeSidebar(restoreFocus = true) {
  const wasOpen = state.sidebarOpen;
  setSidebar(false);
  if (wasOpen && restoreFocus) $('navigation-toggle').focus();
}
function openTaskSearch() {
  if (narrowNavigation.matches) setSidebar(true);
  $('search').focus(); $('search').select();
}
function renderSettings(result) {
  $('settings-connection').textContent = '로컬 컨트롤러 연결됨';
  $('settings-daemon').textContent = result.daemon?.running ? '실행 서비스 가동 중' : '실행 서비스 중지됨';
  $('settings-version').textContent = result.version ? `v${result.version}` : '버전 미기록';
  const fragment = document.createDocumentFragment();
  for (const profile of state.profiles) {
    const row = node('div', null, 'profile-row'), copy = node('div');
    copy.append(node('strong', profile.name), node('span', profile.config?.project || '경로 미기록'));
    row.append(copy, node('span', `${profile.config?.owner || '미지정'} → ${profile.config?.reviewer || '미지정'}`, 'profile-agents')); fragment.append(row);
  }
  if (!state.profiles.length) fragment.append(node('p', '아직 등록된 프로젝트 설정이 없습니다. CLI에서 profile save로 설정을 등록하면 새 작업에 사용할 수 있습니다.', 'empty-inline'));
  $('settings-profiles').replaceChildren(fragment);
}
function applyPreferences() {
  $('preferences-three-d').checked = state.preferences.threeD;
  $('preferences-motion').value = state.preferences.motion;
  document.documentElement.dataset.motion = state.preferences.motion;
  window.dispatchEvent(new CustomEvent('workflow:preferences', { detail: { ...state.preferences } }));
  try { localStorage.setItem('agent-workflow-preferences', JSON.stringify(state.preferences)); } catch (_) { /* optional persistence */ }
}
$('workspace-project').addEventListener('change', () => {
  state.project = $('workspace-project').value;
  try { localStorage.setItem('agent-workflow-project', state.project); } catch (_) { /* optional persistence */ }
  if (!scopedTasks().some(task => task.id === state.selected)) {
    state.selected = scopedTasks()[0]?.id || null; state.detail = null; state.detailKey = '';
  }
  renderProjects(); renderOverview(); renderList(); renderDetail(); renderJobs(); renderNotifications(); loadDetail(state.selected);
});
document.querySelectorAll('.workspace-navigation [data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
$('command-search').addEventListener('click', openTaskSearch);
$('navigation-toggle').addEventListener('click', () => { setSidebar(!state.sidebarOpen); if (state.sidebarOpen) $('navigation-close').focus(); });
$('navigation-backdrop').addEventListener('click', () => closeSidebar());
$('navigation-close').addEventListener('click', () => closeSidebar());
narrowNavigation.addEventListener('change', () => setSidebar(false));
$('preferences-theme').addEventListener('change', () => {
  const theme = $('preferences-theme').value; applyTheme(theme);
  try { localStorage.setItem('agent-workflow-theme', theme); } catch (_) { /* optional persistence */ }
});
$('preferences-three-d').addEventListener('change', () => { state.preferences.threeD = $('preferences-three-d').checked; applyPreferences(); });
$('preferences-motion').addEventListener('change', () => { state.preferences.motion = $('preferences-motion').value; applyPreferences(); });
$('search').addEventListener('input', renderList); $('filter').addEventListener('change', renderList); $('refresh').addEventListener('click', () => { refresh(); window.WorkflowObservatory?.refresh(); });
document.addEventListener('keydown', (event) => {
  const editing = event.target instanceof HTMLElement && (event.target.matches('input, textarea, select') || event.target.isContentEditable);
  if (event.key.toLowerCase() === 'k' && (event.ctrlKey || event.metaKey) && !event.altKey && !document.querySelector('dialog[open]')) { event.preventDefault(); openTaskSearch(); }
  if (event.key === '/' && !editing && !event.ctrlKey && !event.metaKey && !event.altKey && !document.querySelector('dialog[open]')) { event.preventDefault(); openTaskSearch(); }
  if (event.key === 'Escape' && state.sidebarOpen && !document.querySelector('dialog[open]')) { event.preventDefault(); closeSidebar(); }
  if (event.key === 'Tab' && state.sidebarOpen) {
    const controls = [...$('navigation').querySelectorAll('a,button,input,select,[tabindex="0"]')].filter(item => !item.disabled && item.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }
});
setSidebar(false); applyPreferences(); showView(state.view, false);
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
