'use strict';

// The office, graph, and timeline share the controller's recorded trace. UI
// replay only moves a read cursor; it never starts or resumes a workflow.
(() => {
  const root = document.getElementById('observatory');
  if (!root) return;
  const ns = 'http://www.w3.org/2000/svg';
  const statusLabels = { running: '실행 중', succeeded: '정상 종료', failed: '실패', interrupted: '중단됨', unknown: '종료 미확인', idle: '기록 없음', held: '보류', blocked: '확인 필요', pass: '통과', changes_requested: '수정 요청', ready: '구현 제출' };
  const roleLabels = { controller: '컨트롤러', implement: '구현', check: '검사', review: '독립 리뷰', release: '통합·배포' };
  const roles = ['controller', 'implement', 'check', 'review', 'release'];
  const eventLabels = { run_started: '실행 시작', run_finished: '실행 종료', process_started: '실행 지시', process_finished: '결과 반환', agent_started: '작업 지시', implementation_returned: '구현 결과', checks_started: '검사 시작', checks_finished: '검사 결과', review_finished: '리뷰 결과', recovery_started: '복구', completed: '완료 증거', hold_set: '보류', hold_cleared: '보류 해제', task_held: '보류', task_unheld: '보류 해제' };
  const model = { taskId: null, task: null, detail: null, requestedRun: null, data: null, events: [], pagination: null,
    selectedSpan: null, selectedEvent: null, replay: null, playing: null, mode: 'steps', query: '',
    connected: false, error: null, failures: 0, timer: null, abort: null, generation: 0, fetching: false,
    pageLoading: false, lastSync: null, renderKeys: {}, logCache: new Map(), logKind: null, followLatest: true, runOptions: [],
    visible: true, officeMode: '3d', preferences: { threeD: true, motion: 'system' }, officeActors: [] };
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let room = null, roomLoading = null, roomAvailable = null, roomReason = '', roomDisposed = false;
  const el = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text != null) item.textContent = String(text);
    if (className) item.className = className;
    return item;
  };
  const svgEl = (tag, attributes = {}) => {
    const item = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
    return item;
  };
  const safeStatus = value => Object.hasOwn(statusLabels, value) ? value : 'unknown';
  const statusLabel = value => statusLabels[value] || value || '미기록';
  const roleKey = value => ['integration_check', 'deploy', 'health', 'release'].includes(value) ? 'release' : value === 'check' || value === 'checks' ? 'check' : ['implement', 'review'].includes(value) ? value : 'controller';
  const actorRole = actor => actor === 'release' ? 'release' : actor === 'checks' ? 'check' : roleKey(String(actor || '').split(':').pop());
  const actorName = actor => actor === 'controller' ? '컨트롤러' : actor === 'checks' ? '검사 실행기' : actor === 'release' ? '통합·배포' : String(actor || '미기록').replace(/^codex-session(?=:|$)/, 'Codex 독립 세션').replace(/^claude(?=:|$)/, 'Claude').replace(/^codex(?=:|$)/, 'Codex').replace(':implement', ' · 구현').replace(':review', ' · 리뷰');
  const textValue = value => value == null ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  const timestamp = value => { if (value == null || value === '') return null; const n = new Date(value).getTime(); return Number.isFinite(n) ? n : null; };
  const timeLabel = value => timestamp(value) == null ? '시간 미기록' : new Date(value).toLocaleTimeString('ko-KR', { hour12: false });
  const dateLabel = value => timestamp(value) == null ? '시간 미기록' : new Date(value).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  const duration = value => value == null || !Number.isFinite(Number(value)) ? '미기록' : Number(value) < 1000 ? `${Math.round(Number(value))} ms` : Number(value) < 60000 ? `${(Number(value) / 1000).toFixed(1)}초` : `${Math.floor(Number(value) / 60000)}분 ${Math.floor(Number(value) / 1000) % 60}초`;
  const clip = (value, length = 70) => { const text = String(value || ''); return text.length > length ? `${text.slice(0, length)}…` : text; };
  function button(text, key, action, className = '') {
    const item = el('button', text, `obs-button ${className}`.trim());
    item.type = 'button'; item.dataset.focus = key; item.addEventListener('click', action); return item;
  }
  function badge(value, text) { return el('span', text || statusLabel(value), `obs-badge status-${safeStatus(value)}`); }
  function replace(container, content) {
    const active = document.activeElement;
    const focus = container.contains(active) ? active.dataset.focus : null;
    const top = container.scrollTop, left = container.scrollLeft;
    container.replaceChildren(content);
    container.scrollTop = top; container.scrollLeft = left;
    if (focus) [...container.querySelectorAll('[data-focus]')].find(item => item.dataset.focus === focus)?.focus({ preventScroll: true });
  }
  function cached(key, value, draw) {
    const serialized = JSON.stringify(value);
    if (serialized === model.renderKeys[key]) return;
    model.renderKeys[key] = serialized; draw();
  }

  const header = el('div', null, 'obs-heading');
  const headingText = el('div');
  headingText.append(el('span', 'EXECUTION OBSERVATORY', 'section-kicker'), el('h2', '선택한 작업의 실행 기록'));
  const connection = el('span', '기록 연결 대기', 'obs-connection'); connection.setAttribute('role', 'status');
  header.append(headingText, connection);
  const taskLine = el('p', null, 'obs-task-line');
  const toolbar = el('div', null, 'obs-toolbar');
  const runLabel = el('label', '실행 기록', 'obs-run-label');
  const runSelect = el('select'); runSelect.id = 'observatory-run'; runSelect.setAttribute('aria-label', '관측할 실행 기록'); runSelect.dataset.focus = 'obs-run'; runLabel.append(runSelect);
  runSelect.addEventListener('change', () => chooseRun(runSelect.value || null));
  const exportLink = el('a', 'JSON 내보내기', 'obs-button'); exportLink.id = 'observatory-export'; exportLink.setAttribute('download', '');
  const reload = button('기록 새로고침', 'obs-refresh', () => fetchData(), 'obs-subtle'); reload.id = 'observatory-refresh';
  toolbar.append(runLabel, exportLink, reload);
  const notices = el('div', null, 'obs-notices'); notices.setAttribute('aria-live', 'polite');
  const office = el('div', null, 'obs-office'); office.setAttribute('aria-label', '역할별 작업실');
  const officeTop = el('div', null, 'obs-office-top');
  const officeSign = el('h3', '작업 공간', 'obs-office-sign');
  const officeViews = el('div', null, 'obs-room-views'); officeViews.setAttribute('role', 'group'); officeViews.setAttribute('aria-label', '작업 공간 보기');
  const view3d = button('3D 공간', 'obs-view-3d', () => setOfficeMode('3d')); view3d.id = 'observatory-view-3d';
  const viewList = button('역할 목록', 'obs-view-list', () => setOfficeMode('list')); viewList.id = 'observatory-view-list'; officeViews.append(view3d, viewList);
  const officeTime = el('span', '기록 대기', 'obs-office-time'); officeTime.id = 'observatory-room-status'; officeTime.setAttribute('role', 'status');
  officeTop.append(officeSign, officeViews, officeTime);
  const sceneViewport = el('div', null, 'obs-scene-viewport');
  const sceneHost = el('div', null, 'obs-scene-host'); sceneHost.id = 'observatory-scene';
  const sceneFallback = el('div', null, 'obs-scene-fallback'); sceneFallback.id = 'observatory-scene-fallback'; sceneFallback.setAttribute('role', 'status');
  const sceneControls = el('div', null, 'obs-scene-controls'); sceneControls.setAttribute('role', 'group'); sceneControls.setAttribute('aria-label', '3D 카메라 제어');
  const zoomIn = button('+', 'obs-zoom-in', () => room?.zoom(1)); zoomIn.id = 'observatory-zoom-in'; zoomIn.setAttribute('aria-label', '3D 공간 확대');
  const zoomOut = button('−', 'obs-zoom-out', () => room?.zoom(-1)); zoomOut.id = 'observatory-zoom-out'; zoomOut.setAttribute('aria-label', '3D 공간 축소');
  const cameraReset = button('↺', 'obs-camera-reset', () => room?.reset()); cameraReset.id = 'observatory-reset-camera'; cameraReset.setAttribute('aria-label', '3D 카메라 초기화');
  sceneControls.append(zoomIn, zoomOut, cameraReset);
  const sceneLegend = el('div', null, 'obs-scene-legend'); sceneLegend.setAttribute('aria-hidden', 'true');
  sceneLegend.append(el('span', '실행 중', 'is-active'), el('span', '기록 없음', 'is-idle'), el('span', '선택됨', 'is-selected'));
  sceneViewport.append(sceneHost, sceneFallback, sceneControls, sceneLegend);
  const stations = el('div', null, 'obs-stations'); stations.setAttribute('role', 'group'); stations.setAttribute('aria-label', '에이전트와 실행 단계 선택');
  office.append(officeTop, sceneViewport, stations);
  const officeCaption = el('p', '실제 호출과 인계를 표시합니다. 역할을 선택하면 같은 시점의 실행 기록과 연결됩니다.', 'obs-caption obs-office-caption');
  const playback = el('div', null, 'obs-playback');
  const previous = button('←', 'obs-previous', () => stepReplay(-1)); previous.setAttribute('aria-label', '이전 이벤트');
  const play = button('기록 재생', 'obs-play', togglePlayback); play.id = 'observatory-play';
  const next = button('→', 'obs-next', () => stepReplay(1)); next.setAttribute('aria-label', '다음 이벤트');
  const sliderLabel = el('label', '기록 시점', 'sr-only'); sliderLabel.htmlFor = 'observatory-position';
  const slider = el('input'); slider.type = 'range'; slider.id = 'observatory-position'; slider.min = '0'; slider.max = '0'; slider.value = '0'; slider.step = '1';
  slider.addEventListener('input', () => { stopPlayback(); setReplay(model.events[Number(slider.value)]?.seq ?? null); });
  const replayLabel = el('span', '실시간 기록', 'obs-replay-label'); replayLabel.id = 'observatory-replay-label';
  const live = button('실시간으로', 'obs-live', () => { stopPlayback(); model.followLatest = true; setReplay(null); }, 'obs-live'); live.id = 'observatory-live';
  playback.append(previous, play, next, sliderLabel, slider, replayLabel, live);
  const workspace = el('div', null, 'obs-workspace');
  const officePanel = el('section', null, 'obs-office-panel'); officePanel.append(office, officeCaption);
  const explorer = el('div', null, 'obs-explorer');
  const flow = el('section', null, 'obs-flow'); flow.setAttribute('aria-label', '실행 그래프');
  const flowHeading = el('div', null, 'obs-panel-heading'); flowHeading.append(el('h3', '실행 흐름'));
  const modeButtons = el('div', null, 'obs-segmented'); modeButtons.setAttribute('role', 'group'); modeButtons.setAttribute('aria-label', '그래프 표시 방식');
  const stepsButton = button('실행 순서', 'obs-mode-steps', () => { model.mode = 'steps'; render(); }); stepsButton.id = 'observatory-mode-steps';
  const rolesButton = button('역할 개요', 'obs-mode-roles', () => { model.mode = 'roles'; render(); }); rolesButton.id = 'observatory-mode-roles'; modeButtons.append(stepsButton, rolesButton); flowHeading.append(modeButtons);
  const graphHint = el('p', null, 'obs-caption');
  const graph = el('div', null, 'obs-graph-viewport'); graph.id = 'observatory-graph'; graph.tabIndex = 0; graph.setAttribute('aria-label', '실행 흐름 그래프, 방향키로 스크롤 가능');
  const graphLegend = el('p', '실선: 컨트롤러를 통한 역할 간 인계 · 점선: 같은 역할의 다음 실행', 'obs-graph-legend');
  flow.append(flowHeading, graphHint, graph, graphLegend);
  const inspector = el('section', null, 'obs-inspector'); inspector.id = 'observatory-inspector'; inspector.setAttribute('aria-label', '선택한 실행 기록 상세');
  workspace.append(officePanel, inspector);
  explorer.append(flow);
  const eventSection = el('section', null, 'obs-event-section'); eventSection.setAttribute('aria-label', '선택한 실행의 인계 기록');
  const eventHeading = el('div', null, 'obs-panel-heading');
  const eventHeadingText = el('div'); eventHeadingText.append(el('h3', '인계 기록'), el('p', '선택한 실행에서 실제로 전달한 지시와 반환된 결과', 'obs-caption'));
  const searchLabel = el('label', null, 'obs-event-search'); searchLabel.append(el('span', '이벤트 검색', 'sr-only'));
  const search = el('input'); search.type = 'search'; search.id = 'observatory-search'; search.placeholder = '역할 · 내용 · 순번 검색'; search.setAttribute('aria-label', '이벤트 검색'); search.addEventListener('input', () => { model.query = search.value; renderEvents(); }); searchLabel.append(search); eventHeading.append(eventHeadingText, searchLabel);
  const eventList = el('ol', null, 'obs-events'); eventList.id = 'observatory-events';
  const eventFooter = el('div', null, 'obs-event-footer');
  const older = button('이전 기록 불러오기', 'obs-older', loadOlder); older.id = 'observatory-older';
  const eventCount = el('span', '', 'obs-caption'); eventCount.setAttribute('aria-live', 'polite'); eventFooter.append(older, eventCount);
  eventSection.append(eventHeading, playback, eventList, eventFooter);
  const coverage = el('p', '관측 범위: agent-workflow가 실행한 단계와 결과. CLI 내부의 개별 도구 호출·하위 에이전트 대화는 이 화면의 기록 범위에 포함되지 않습니다.', 'obs-coverage');
  root.append(header, taskLine, toolbar, notices, workspace, explorer, eventSection, coverage);
  root.hidden = true;

  // Original compact robot marks are an accessible visual counterpart to the
  // procedural 3D agents. Their colour follows the role, never a provider logo.
  function avatar(role) {
    const image = svgEl('svg', { viewBox: '0 0 40 40', 'aria-hidden': 'true', class: `obs-avatar role-${role}` });
    image.append(svgEl('path', { d: 'M20 4V1', class: 'robot-antenna' }),
      svgEl('circle', { cx: 20, cy: 2, r: 2, class: 'robot-light' }),
      svgEl('rect', { x: 3, y: 7, width: 34, height: 28, rx: 12, class: 'robot-shell' }),
      svgEl('rect', { x: 7, y: 12, width: 26, height: 17, rx: 8, class: 'robot-visor' }),
      svgEl('rect', { x: 13, y: 17, width: 3, height: 7, rx: 1.5, class: 'robot-light' }),
      svgEl('rect', { x: 24, y: 17, width: 3, height: 7, rx: 1.5, class: 'robot-light' }),
      svgEl('path', { d: 'M12 35V38M28 35V38', class: 'robot-antenna' }));
    return image;
  }
  function displayStatus(span) {
    if (span.status === 'running' || span.status === 'unknown' || span.status === 'interrupted' || span.status === 'failed') return span.status;
    if (span.result?.verdict === 'changes_requested' || span.result?.outcome === 'changes_requested') return 'changes_requested';
    if (span.result?.passed === false || span.result?.outcome === 'blocked') return 'blocked';
    if (span.result?.verdict === 'pass' || span.result?.passed === true) return 'pass';
    return safeStatus(span.status);
  }
  function visibleEvents() { return model.replay == null ? model.events : model.events.filter(event => Number(event.seq) <= model.replay); }
  function visibleSpans() {
    const spans = model.data?.spans || [];
    if (model.replay == null) return spans;
    const cursor = model.events.find(event => Number(event.seq) === model.replay);
    if (!cursor) return [];
    const cursorTime = timestamp(cursor.at);
    return spans.flatMap(span => {
      const related = model.events.filter(event => event.span_id === span.id);
      const start = related.find(event => event.kind === 'process_started');
      const finish = related.find(event => event.kind === 'process_finished');
      const startTime = timestamp(span.started_at), endTime = timestamp(span.ended_at);
      if (start ? Number(start.seq) > model.replay : startTime == null || cursorTime == null || startTime > cursorTime) return [];
      const ended = finish ? Number(finish.seq) <= model.replay : endTime != null && cursorTime != null && endTime < cursorTime;
      const ambiguous = !start && startTime === cursorTime || !finish && endTime === cursorTime;
      const resultEvent = related.filter(event => ['implementation_returned', 'review_finished', 'checks_finished', 'agent_failed'].includes(event.kind)).at(-1);
      const result = resultEvent && Number(resultEvent.seq) <= model.replay ? span.result : null;
      return [{ ...span, status: ambiguous ? 'unknown' : ended ? span.status : 'running', result,
        ended_at: ended ? span.ended_at : null,
        duration_ms: ended ? span.duration_ms : startTime != null && cursorTime != null ? Math.max(0, cursorTime - startTime) : null,
        process: ended ? span.process : null, usage: ended ? span.usage : null, error: ended ? span.error : null, replay_uncertain: ambiguous }];
    });
  }
  function selectedRecord() {
    const spans = visibleSpans(), events = visibleEvents();
    const event = events.find(item => Number(item.seq) === model.selectedEvent);
    return { span: spans.find(item => item.id === (event?.span_id || model.selectedSpan)), event };
  }
  function selectSpan(id) {
    model.followLatest = false; model.selectedSpan = id; model.selectedEvent = null; model.logKind = null; render();
  }
  function selectEvent(event) {
    model.followLatest = false; model.selectedEvent = Number(event.seq); model.selectedSpan = event.span_id || null; model.logKind = null; render();
  }
  function setReplay(seq) {
    model.replay = seq == null ? null : Number(seq);
    if (model.replay != null) {
      const event = model.events.find(item => Number(item.seq) === model.replay);
      model.selectedEvent = model.replay; model.selectedSpan = event?.span_id || null;
    } else if (model.followLatest) {
      model.selectedEvent = null; model.selectedSpan = model.data?.spans?.at(-1)?.id || null;
    }
    model.logKind = null; render();
  }
  function stopPlayback() { if (model.playing) clearInterval(model.playing); model.playing = null; play.textContent = '기록 재생'; }
  function stepReplay(direction) {
    stopPlayback();
    if (!model.events.length) return;
    const current = model.replay == null ? model.events.length - 1 : model.events.findIndex(event => Number(event.seq) === model.replay);
    const index = Math.max(0, Math.min(model.events.length - 1, current + direction));
    setReplay(model.events[index].seq);
  }
  function togglePlayback() {
    if (model.playing) { stopPlayback(); return; }
    if (!model.events.length) return;
    if (model.replay == null || model.replay >= Number(model.events.at(-1).seq)) setReplay(model.events[0].seq);
    model.playing = setInterval(() => {
      const index = model.events.findIndex(event => Number(event.seq) === model.replay);
      if (index >= model.events.length - 1) { stopPlayback(); return; }
      setReplay(model.events[index + 1].seq);
    }, 1000);
    play.textContent = '재생 일시정지';
  }

  function reducedMotion() { return model.preferences.motion === 'reduce' || motionQuery.matches; }
  function officeVisible() { return model.visible && !document.hidden && Boolean(model.taskId) && model.preferences.threeD && model.officeMode === '3d'; }
  function selectActor(actorId) {
    const actor = model.officeActors.find(item => item.id === actorId);
    if (actor?.spanId) selectSpan(actor.spanId);
    else if (actor?.event) selectEvent(actor.event);
  }
  function setOfficeMode(mode) {
    if (mode === '3d' && !model.preferences.threeD) return;
    model.officeMode = mode;
    renderOffice(visibleSpans());
  }
  function updateRoomPresentation() {
    const threeD = model.preferences.threeD && model.officeMode === '3d';
    sceneViewport.hidden = !threeD;
    view3d.setAttribute('aria-pressed', String(threeD));
    viewList.setAttribute('aria-pressed', String(!threeD));
    view3d.disabled = !model.preferences.threeD;
    view3d.title = model.preferences.threeD ? '3D 작업 공간 보기' : '환경 설정에서 3D 공간을 켤 수 있습니다.';
    office.classList.toggle('is-list-view', !threeD);
    office.classList.toggle('is-scene-unavailable', roomAvailable === false);
    office.dataset.sceneState = !threeD ? 'list' : roomAvailable === true ? 'ready' : roomAvailable === false ? 'unavailable' : 'loading';
    sceneFallback.hidden = roomAvailable === true;
    if (!sceneFallback.hidden) {
      const fragment = document.createDocumentFragment();
      fragment.append(el('span', roomAvailable === false ? '◇' : '◈', 'obs-scene-placeholder-icon'),
        el('strong', roomAvailable === false ? '역할 목록으로 계속 확인할 수 있습니다.' : '3D 작업 공간을 준비하고 있습니다.'),
        el('p', roomAvailable === false ? '이 환경에서 3D 화면을 열지 못했습니다. 아래 역할과 실행 그래프는 같은 기록을 보여줍니다.' : '실제 실행 기록을 작업 공간에 연결합니다.'));
      if (roomAvailable === false) {
        const retry = button('3D 다시 시도', 'obs-scene-retry', retryRoom); retry.id = 'observatory-scene-retry'; fragment.append(retry);
        if (roomReason) { const details = el('details'); details.append(el('summary', '표시 오류 상세'), el('p', clip(roomReason, 350))); fragment.append(details); }
      }
      replace(sceneFallback, fragment);
    }
    for (const control of [zoomIn, zoomOut, cameraReset]) control.disabled = roomAvailable !== true;
    sceneLegend.hidden = roomAvailable !== true;
  }
  let roomEpoch = 0;
  async function ensureRoom() {
    if (room || roomLoading || roomAvailable === false || roomDisposed || !officeVisible()) return;
    const epoch = roomEpoch;
    roomLoading = import(`/static/scene3d.js${epoch ? `?retry=${epoch}` : ''}`);
    try {
      const module = await roomLoading;
      if (roomDisposed || epoch !== roomEpoch || !officeVisible()) return;
      room = module.createOffice(sceneHost, {
        onSelectActor: selectActor,
        onAvailabilityChange(result) {
          if (roomDisposed || epoch !== roomEpoch) return;
          roomAvailable = result.available === true; roomReason = result.reason || '';
          updateRoomPresentation();
        }
      });
      updateRoom();
    } catch (error) {
      if (epoch !== roomEpoch || roomDisposed) return;
      roomAvailable = false; roomReason = error.message || '3D 모듈을 불러오지 못했습니다.';
      sceneHost.dataset.sceneStatus = 'unavailable'; updateRoomPresentation();
    } finally { if (epoch === roomEpoch) roomLoading = null; }
  }
  function retryRoom() {
    roomEpoch += 1; roomLoading = null;
    const previous = room; room = null; previous?.dispose();
    sceneHost.replaceChildren(); roomAvailable = null; roomReason = ''; updateRoomPresentation(); ensureRoom();
  }
  function updateRoom() {
    const spans = visibleSpans(), bySpan = new Map(spans.map(span => [span.id, span.actor])), grouped = new Map();
    for (const edge of model.data?.edges || []) {
      const source = bySpan.get(edge.source), target = bySpan.get(edge.target);
      if (!source || !target) continue;
      const key = `${source}\u0000${target}`, previous = grouped.get(key);
      if (previous) previous.count += 1;
      else grouped.set(key, { source, target, kind: edge.kind, count: 1 });
    }
    const selected = selectedRecord();
    const selectedActor = selected.span?.actor || selected.event?.actor || null;
    const actors = model.officeActors.map(({ event, ...actor }) => actor);
    room?.update({ actors, selectedActor, edges: [...grouped.values()], connected: model.connected,
      replaying: model.replay != null, visible: officeVisible(), reducedMotion: reducedMotion(),
      theme: document.documentElement.dataset.theme || 'light' });
    room?.setVisible(officeVisible());
    updateRoomPresentation();
    ensureRoom();
  }
  function renderOffice(spans) {
    const data = model.data, definitions = data?.actors || [];
    const actors = definitions.length ? definitions : [
      { id: 'controller', label: '컨트롤러', role: 'controller' },
      { id: `${model.task?.owner || '미지정'}:implement`, label: model.task?.owner || '미지정', role: 'implement' },
      { id: 'checks', label: '검사 실행기', role: 'check' },
      ...(model.task?.reviewer === 'none' ? [] : [{ id: `${model.task?.reviewer || '미지정'}:review`, label: model.task?.reviewer || '미지정', role: 'review' }]),
      { id: 'release', label: '통합·배포', role: 'release' }
    ];
    const ordered = [...actors].sort((a, b) => roles.indexOf(roleKey(a.role)) - roles.indexOf(roleKey(b.role)));
    const selected = selectedRecord(), selectedActor = selected.span?.actor || selected.event?.actor;
    const events = visibleEvents();
    model.officeActors = ordered.map(actor => {
      const related = spans.filter(span => span.actor === actor.id), latest = related.at(-1), role = roleKey(actor.role);
      const latestEvent = events.filter(event => event.actor === actor.id || event.recipient === actor.id).at(-1);
      let status = latest ? displayStatus(latest) : 'idle';
      if (model.replay == null && model.requestedRun == null && data?.task?.hold && !latest) status = 'held';
      const label = actor.label === 'codex-session' ? 'Codex · 독립 세션' : actor.label === 'claude' ? 'Claude' : actor.label === 'codex' ? 'Codex' : actor.label || actorName(actor.id);
      return { id: actor.id, label, role, status, active: status === 'running' && model.connected && model.replay == null && model.visible && !document.hidden,
        selected: actor.id === selectedActor, spanId: latest?.id || null, event: latestEvent || null, count: related.length,
        goal: latest?.input?.goal || model.task?.goal || '', caption: latest?.label || latestEvent?.summary || '관측된 실행 없음' };
    });
    cached('office', [model.officeActors, model.replay, model.connected, Boolean(model.data)], () => {
      const fragment = document.createDocumentFragment();
      for (const actor of model.officeActors) {
        const station = button('', `obs-actor-${actor.id}`, () => selectActor(actor.id), `obs-station role-${actor.role}${actor.active ? ' is-active' : ''}${actor.selected ? ' is-selected' : ''}`);
        station.disabled = !actor.spanId && !actor.event; station.dataset.actor = actor.id;
        const currentLabel = !model.connected && model.data && actor.status === 'running' ? '이전 기록' : model.replay != null && actor.status === 'running' ? '당시 실행 중' : actor.event && !actor.spanId && actor.status === 'idle' ? '활동 기록' : statusLabel(actor.status);
        station.setAttribute('aria-label', `${roleLabels[actor.role]} ${actor.label}, ${currentLabel}${actor.spanId ? ', 최근 실행 상세 보기' : ''}`);
        station.setAttribute('aria-pressed', String(actor.selected));
        const body = el('span', null, 'obs-station-body');
        const name = el('span', null, 'obs-station-top'); name.append(el('span', actor.label, 'obs-station-name'), badge(actor.status, currentLabel));
        body.append(name, el('span', `${roleLabels[actor.role]} · ${actor.count}회 호출`, 'obs-station-task'));
        station.append(avatar(actor.role), body); fragment.append(station);
      }
      replace(stations, fragment);
    });
    office.classList.toggle('is-replay', model.replay != null);
    office.classList.toggle('is-disconnected', !model.connected);
    root.dataset.motion = reducedMotion() ? 'reduced' : 'full';
    officeTime.textContent = model.replay != null ? `기록 재생 · ${timeLabel(model.events.find(event => Number(event.seq) === model.replay)?.at)}` : model.connected ? '연결됨' : model.data ? '이전 기록' : '연결 중';
    updateRoom();
  }

  function graphNode(span, index, aggregate = false, selected = model.selectedSpan === span.id) {
    const status = displayStatus(span), role = roleKey(span.role);
    const item = button('', `obs-span-${span.id}`, () => selectSpan(span.id), `obs-node role-${role} status-${safeStatus(status)}${selected ? ' is-selected' : ''}${status === 'running' && model.connected && model.replay == null ? ' is-running' : ''}`);
    item.dataset.spanId = span.id;
    item.title = `${actorName(span.actor)} · ${span.label}\n${statusLabel(status)} · ${dateLabel(span.started_at)}`;
    item.setAttribute('aria-label', `${aggregate ? '역할' : `${index + 1}단계`} ${actorName(span.actor)}, ${span.label}, ${statusLabel(status)}, 상세 보기`);
    const top = el('span', null, 'obs-node-top'); top.append(el('span', aggregate ? `${span.count}회` : String(index + 1).padStart(2, '0'), 'obs-node-number'), el('span', statusLabel(status), 'obs-node-state'));
    item.append(top, el('span', clip(aggregate ? actorName(span.actor) : span.label, 40), 'obs-node-label'), el('span', aggregate ? `정상 ${span.passed} · 확인 ${span.attention}` : actorName(span.actor), 'obs-node-actor'));
    return item;
  }
  function renderGraph(spans) {
    const layout = { compact: window.matchMedia('(max-width: 640px)').matches, viewportWidth: graph.clientWidth, mode: model.mode };
    const layoutKey = JSON.stringify(layout);
    const resized = model.renderKeys.graphLayout != null && model.renderKeys.graphLayout !== layoutKey;
    cached('graph', [spans, model.data?.edges, model.mode, model.selectedSpan, model.connected, model.replay, layout], () => drawGraph(spans, layout, resized));
    model.renderKeys.graphLayout = layoutKey;
  }
  function drawGraph(spans, layout, resized) {
    const fragment = document.createDocumentFragment();
    if (!spans.length) {
      const empty = el('div', null, 'obs-empty');
      empty.append(el('span', '↗', 'obs-empty-mark'), el('h4', model.data?.legacy ? '기존 기록을 시간순으로 확인하세요.' : '첫 실행을 기다리고 있습니다.'),
        el('p', model.data?.legacy ? '단계별 관측 이전의 작업입니다. 아래 이벤트는 보존하며, 없던 실행 시간이나 대화를 만들지 않습니다.' : '워크플로를 실행하면 실제 구현·검사·독립 리뷰가 순서대로 연결됩니다.'));
      fragment.append(empty); replace(graph, fragment); return;
    }
    const { compact } = layout;
    const previousSelected = graph.querySelector('.obs-node.is-selected');
    const previousId = previousSelected?.dataset.spanId;
    const previousOffset = previousSelected ? previousSelected.offsetTop - graph.scrollTop : null;
    const canvas = el('div', null, `obs-graph-canvas${model.mode === 'roles' ? ' is-aggregate' : ''}${compact ? ' is-compact' : ''}`);
    let graphNodes, edges;
    if (model.mode === 'roles') {
      const groups = new Map();
      for (const span of spans) { if (!groups.has(span.actor)) groups.set(span.actor, []); groups.get(span.actor).push(span); }
      graphNodes = [...groups].map(([actor, items]) => ({ ...items.at(-1), actor, count: items.length,
        passed: items.filter(span => ['succeeded', 'pass'].includes(displayStatus(span))).length,
        attention: items.filter(span => ['failed', 'blocked', 'changes_requested', 'unknown', 'interrupted'].includes(displayStatus(span))).length }));
      const bySpan = new Map(spans.map(span => [span.id, span.actor]));
      const byActor = new Map(graphNodes.map(span => [span.actor, span]));
      const grouped = new Map();
      for (const edge of model.data?.edges || []) {
        const source = bySpan.get(edge.source), target = bySpan.get(edge.target);
        if (!source || !target) continue;
        const key = `${source}\u0000${target}`, previous = grouped.get(key);
        if (previous) previous.count += 1;
        else grouped.set(key, { ...edge, source: byActor.get(source).id, target: byActor.get(target).id, count: 1 });
      }
      edges = [...grouped.values()];
    } else { graphNodes = spans; edges = model.data?.edges || []; }
    const nodeWidth = compact ? Math.min(252, Math.max(1, layout.viewportWidth - 64)) : 156;
    const nodeHeight = compact ? 82 : 88, gap = compact ? 30 : 28;
    const requiredWidth = graphNodes.length * (nodeWidth + gap) - gap + 40;
    const width = compact ? Math.max(1, layout.viewportWidth) : Math.max(layout.viewportWidth, requiredWidth);
    const start = compact ? (width - nodeWidth) / 2 : Math.max(20, (width - requiredWidth + 40) / 2);
    const positions = new Map();
    graphNodes.forEach((span, index) => positions.set(span.id, compact
      ? { x: start, y: 28 + index * (nodeHeight + gap) }
      : { x: start + index * (nodeWidth + gap), y: model.mode === 'roles' ? 60 : 28 }));
    const selectedActor = spans.find(span => span.id === model.selectedSpan)?.actor;
    const selectedNodeId = model.mode === 'roles' ? graphNodes.find(span => span.actor === selectedActor)?.id : model.selectedSpan;
    const height = compact ? Math.max(180, graphNodes.length * (nodeHeight + gap) + 28) : model.mode === 'roles' ? 182 : 140;
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    const drawing = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, width, height, 'aria-hidden': 'true', class: 'obs-edges' });
    const defs = svgEl('defs');
    const marker = svgEl('marker', { id: 'observatory-arrow', viewBox: '0 0 8 8', refX: 6, refY: 4, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' });
    marker.append(svgEl('path', { d: 'M1 1L6 4L1 7', fill: 'none', stroke: 'context-stroke', 'stroke-width': 1.5 })); defs.append(marker); drawing.append(defs);
    if (compact) drawing.append(svgEl('path', { d: `M${width / 2} 16V${height - 12}`, class: 'obs-lane-line' }));
    for (const edge of edges) {
      const from = positions.get(edge.source), to = positions.get(edge.target);
      if (!from || !to) continue;
      let d, tx, ty;
      if (model.mode === 'roles') {
        if (compact) {
          const forward = to.y >= from.y, x = from.x + (forward ? nodeWidth : 0), rail = forward ? width - 12 : 12;
          if (edge.source === edge.target) {
            d = `M${x} ${from.y + 24}C${rail} ${from.y - 12},${rail} ${from.y + nodeHeight + 12},${x} ${from.y + nodeHeight - 18}`;
            tx = (x + 3 * rail) / 4; ty = from.y + nodeHeight / 2;
          } else {
            const y1 = from.y + nodeHeight * (forward ? .7 : .3), y2 = to.y + nodeHeight * (forward ? .3 : .7);
            d = `M${x} ${y1}C${rail} ${y1},${rail} ${y2},${x} ${y2}`;
            tx = (x + 3 * rail) / 4; ty = (y1 + y2) / 2;
          }
        } else if (edge.source === edge.target) {
          d = `M${from.x + nodeWidth * .65} ${from.y}C${from.x + nodeWidth + 30} ${from.y - 49},${from.x + nodeWidth + 30} ${from.y + nodeHeight + 28},${from.x + nodeWidth} ${from.y + nodeHeight * .65}`;
          tx = from.x + nodeWidth + 15; ty = from.y + 10;
        } else {
          const forward = to.x > from.x, x1 = from.x + nodeWidth * (forward ? .65 : .35), x2 = to.x + nodeWidth * (forward ? .35 : .65);
          const y = Math.min(from.y, to.y) - (forward ? 30 : 56);
          d = `M${x1} ${from.y}C${x1} ${y},${x2} ${y},${x2} ${to.y}`; tx = (x1 + x2) / 2; ty = y + 7;
        }
      } else if (compact) {
        const x1 = from.x + nodeWidth / 2, y1 = from.y + nodeHeight, x2 = to.x + nodeWidth / 2, y2 = to.y, middle = (y1 + y2) / 2;
        d = `M${x1} ${y1}C${x1} ${middle},${x2} ${middle},${x2} ${y2}`;
      } else {
        const x1 = from.x + nodeWidth, y1 = from.y + nodeHeight / 2, x2 = to.x, y2 = to.y + nodeHeight / 2, middle = (x1 + x2) / 2;
        d = `M${x1} ${y1}C${middle} ${y1},${middle} ${y2},${x2} ${y2}`;
      }
      const line = svgEl('path', { d, class: `obs-edge ${edge.kind === 'handoff' ? 'is-handoff' : 'is-sequence'}${[edge.source, edge.target].includes(selectedNodeId) ? ' is-selected' : ''}`, 'marker-end': 'url(#observatory-arrow)' });
      const title = svgEl('title'); title.textContent = `${edge.label || '컨트롤러의 다음 실행'}${edge.count ? ` · ${edge.count}회` : ''}`; line.append(title); drawing.append(line);
      if (model.mode === 'roles' && edge.count) { const label = svgEl('text', { x: tx, y: ty, 'text-anchor': 'middle', class: 'obs-edge-count' }); label.textContent = String(edge.count); drawing.append(label); }
    }
    canvas.prepend(drawing);
    const order = new Map(spans.map((span, index) => [span.id, index]));
    graphNodes.forEach((span, index) => {
      const item = graphNode(span, order.get(span.id) ?? index, model.mode === 'roles', selectedNodeId === span.id), position = positions.get(span.id);
      item.style.left = `${position.x}px`; item.style.top = `${position.y}px`; item.style.width = `${nodeWidth}px`; item.style.height = `${nodeHeight}px`; canvas.append(item);
    });
    fragment.append(canvas); replace(graph, fragment);
    if (positions.has(selectedNodeId) && (resized || previousId !== selectedNodeId || model.followLatest && model.replay == null)) {
      const selected = positions.get(selectedNodeId);
      const anchor = resized && previousOffset != null ? Math.max(12, Math.min(previousOffset, graph.clientHeight - nodeHeight - 12)) : (graph.clientHeight - nodeHeight) / 2;
      graph.scrollTop = Math.max(0, selected.y - anchor);
      graph.scrollLeft = Math.max(0, selected.x - (graph.clientWidth - nodeWidth) / 2);
    }
    if (compact) graph.scrollLeft = 0;
  }

  function addField(list, title, value) {
    if (value == null || value === '') return;
    const row = el('div'); row.append(el('dt', title), el('dd', textValue(value))); list.append(row);
  }
  function inspectorBlock(title, value) {
    const block = el('div', null, 'obs-inspector-block'); block.append(el('h4', title), el('p', textValue(value))); return block;
  }
  function drawInspector() {
    const { span, event } = selectedRecord();
    const recordKey = `${span?.id || ''}:${event?.seq || ''}`;
    const opened = inspector.dataset.recordKey === recordKey ? new Set([...inspector.querySelectorAll('details[open]')].map(item => item.dataset.recordDetail)) : new Set();
    const fragment = document.createDocumentFragment(), heading = el('div', null, 'obs-panel-heading');
    heading.append(el('h3', span ? '선택한 에이전트' : '선택한 기록'));
    if (!span && !event) {
      fragment.append(heading, el('div', '작업 공간의 에이전트나 아래 실행 단계를 선택하세요. 목표, 반환된 결과와 실행 근거를 함께 확인할 수 있습니다.', 'obs-inspector-placeholder'));
      replace(inspector, fragment); return;
    }
    const status = event?.status || (span ? displayStatus(span) : null);
    if (status) heading.append(badge(status)); fragment.append(heading);
    if (span) {
      const identity = el('div', null, `obs-agent-identity role-${roleKey(span.role)}`), name = el('div');
      name.append(el('strong', actorName(span.actor)), el('span', span.label));
      identity.append(avatar(roleKey(span.role)), name); fragment.append(identity);
      if (span.input?.goal) fragment.append(inspectorBlock('현재 실행의 목표', span.input.goal));
      const summary = el('dl', null, 'obs-summary-facts');
      let elapsed = span.duration_ms;
      if (elapsed == null && span.status === 'running' && model.replay == null) {
        const start = timestamp(span.started_at), observed = timestamp(model.lastSync);
        if (start != null && observed != null) elapsed = Math.max(0, observed - start);
      }
      addField(summary, '역할', roleLabels[roleKey(span.role)]); addField(summary, span.status === 'running' ? '관측 경과' : '소요 시간', duration(elapsed));
      addField(summary, '모델', span.model || '미기록'); fragment.append(summary);
      if (span.replay_uncertain) fragment.append(el('p', '이전 이벤트 페이지에 정확한 시작·종료 순번이 없습니다. 같은 초의 경계는 확정하지 않습니다.', 'obs-inline-warning'));
      if (span.error) fragment.append(inspectorBlock('실행 오류', span.error));
      if (span.result?.summary) fragment.append(inspectorBlock('반환된 요약', span.result.summary));
      if (span.result?.verdict || span.result?.outcome) fragment.append(inspectorBlock('판정', statusLabel(span.result.verdict || span.result.outcome)));
      if (span.result?.next_action) fragment.append(inspectorBlock('다음 행동', span.result.next_action));
      if (span.result?.findings?.length) {
        const block = el('div', null, 'obs-inspector-block'), list = el('ul'); block.append(el('h4', '리뷰 지적'));
        for (const finding of span.result.findings) list.append(el('li', `${finding.blocking ? '[차단] ' : ''}${finding.detail || textValue(finding)}${finding.evidence ? `\n근거: ${finding.evidence}` : ''}`));
        block.append(list); fragment.append(block);
      }
      if (span.artifacts?.length) {
        const block = el('div', null, 'obs-inspector-block obs-logs-block'); block.append(el('h4', '실행 로그'));
        const controls = el('div', null, 'obs-log-controls');
        for (const kind of ['stdout', 'stderr'].filter(kind => span.artifacts.includes(kind))) controls.append(button(kind === 'stdout' ? '출력 로그 열기' : '오류 로그 열기', `obs-log-${kind}`, () => loadLog(span.id, kind)));
        block.append(controls);
        if (model.replay != null) block.append(el('p', '현재 저장된 로그를 읽습니다. 재생 시점 이후 내용이 포함될 수 있습니다.', 'obs-caption'));
        if (model.logKind) {
          const cached = model.logCache.get(`${span.id}:${model.logKind}`);
          if (cached) block.append(el('pre', cached, 'obs-log'));
        }
        fragment.append(block);
      }
      if (span.input?.criteria?.length) {
        const block = el('details', null, 'obs-record-details'), list = el('ul'); block.dataset.recordDetail = 'criteria';
        const summary = el('summary', `완료 조건 ${span.input.criteria.length}개`); summary.dataset.focus = 'obs-criteria'; block.append(summary);
        for (const criterion of span.input.criteria) list.append(el('li', textValue(criterion)));
        block.append(list); fragment.append(block);
      }
      const details = el('details', null, 'obs-record-details'); details.dataset.recordDetail = 'evidence';
      const evidenceSummary = el('summary', '실행 근거와 사용량'); evidenceSummary.dataset.focus = 'obs-evidence'; details.append(evidenceSummary);
      const fields = el('dl', null, 'obs-fields');
      addField(fields, '실행 ID', span.run_id); addField(fields, '단계 ID', span.id);
      addField(fields, '시작', dateLabel(span.started_at)); addField(fields, '종료', span.ended_at ? dateLabel(span.ended_at) : model.replay != null ? '이 시점에는 종료 기록 없음' : '종료 기록 없음');
      if (span.process?.exit_code != null) addField(fields, '종료 코드', span.process.exit_code);
      if (span.process?.timed_out) addField(fields, '시간 제한', '초과');
      if (span.usage?.tokens != null) addField(fields, '보고된 토큰', span.usage.tokens);
      addField(fields, '보고된 비용', span.usage?.reported_cost_usd != null ? `$${Number(span.usage.reported_cost_usd).toFixed(6)}` : '미보고');
      details.append(fields); fragment.append(details);
      fragment.append(el('p', '과거 실행 결과와 현재 작업의 검증 상태는 다를 수 있습니다. 작업 상세에서 최신 증거를 확인하세요.', 'obs-caption'));
    }
    if (event) {
      const eventBlock = el('div', null, 'obs-selected-event');
      eventBlock.append(el('p', `#${event.seq} · ${eventLabels[event.kind] || event.kind}`, 'obs-record-id'), inspectorBlock('선택한 이벤트', event.summary || eventLabels[event.kind] || event.kind));
      if (event.recipient) eventBlock.append(inspectorBlock('전달 경로', `${actorName(event.actor)} → ${actorName(event.recipient)}`));
      fragment.append(eventBlock);
      if (event.data && Object.keys(event.data).length) {
        const block = el('details', null, 'obs-event-data'); block.dataset.recordDetail = 'event';
        const summary = el('summary', '구조화된 이벤트 데이터'); summary.dataset.focus = 'obs-event-data';
        block.append(summary, el('pre', textValue(event.data))); fragment.append(block);
      }
    }
    for (const detail of fragment.querySelectorAll('details')) detail.open = opened.has(detail.dataset.recordDetail);
    inspector.dataset.recordKey = recordKey;
    replace(inspector, fragment);
  }
  async function loadLog(spanId, kind) {
    const generation = model.generation;
    model.logKind = kind; model.logCache.set(`${spanId}:${kind}`, '로그를 불러오고 있습니다…'); model.renderKeys.inspector = null; renderInspector();
    try {
      const value = await fetchJSON(`/api/observability/artifacts/${encodeURIComponent(spanId)}/${kind}`);
      if (generation !== model.generation) return;
      model.logCache.set(`${spanId}:${kind}`, `${value.truncated ? '[최근 64 KiB]\n' : ''}${value.text || '(저장된 출력 없음)'}`);
    } catch (error) { if (generation === model.generation) model.logCache.set(`${spanId}:${kind}`, error.message); }
    if (generation === model.generation) { model.renderKeys.inspector = null; renderInspector(); }
  }
  function renderInspector() { const selected = selectedRecord(); cached('inspector', [selected, model.logKind, model.replay, selected.span?.status === 'running' ? model.lastSync : null], drawInspector); }

  function renderEvents() {
    const current = visibleEvents(), query = model.query.trim().toLocaleLowerCase();
    const filtered = current.filter(event => !query || [event.seq, event.summary, event.kind, event.actor, event.recipient, actorName(event.actor), actorName(event.recipient)].join(' ').toLocaleLowerCase().includes(query));
    cached('events', [filtered, model.selectedEvent, model.selectedSpan, model.replay], () => {
      const fragment = document.createDocumentFragment();
      for (const event of filtered) {
        const row = el('li');
        const item = button('', `obs-event-${event.seq}`, () => selectEvent(event), `obs-event ${event.recipient ? 'has-handoff' : ''}${model.selectedEvent === Number(event.seq) ? ' is-selected' : ''}`);
        item.dataset.eventSeq = String(event.seq);
        const number = el('span', `#${event.seq}`, 'obs-event-sequence');
        const body = el('span', null, 'obs-event-body');
        const route = el('span', null, 'obs-event-route');
        route.append(el('span', actorName(event.actor), `obs-actor-label role-${actorRole(event.actor)}`));
        if (event.recipient) route.append(el('span', '→', 'obs-route-arrow'), el('span', actorName(event.recipient), `obs-actor-label role-${actorRole(event.recipient)}`));
        route.append(el('span', eventLabels[event.kind] || event.kind, 'obs-event-kind'));
        body.append(route, el('span', event.summary || eventLabels[event.kind] || event.kind, 'obs-event-summary'));
        const end = el('span', null, 'obs-event-end'); const time = el('time', timeLabel(event.at)); if (event.at) time.dateTime = event.at; end.append(time);
        if (event.status) end.append(badge(event.status));
        item.append(number, body, end); row.append(item); fragment.append(row);
      }
      if (!filtered.length) fragment.append(el('li', query ? '검색에 맞는 기록이 없습니다.' : model.data ? '이 실행의 이벤트가 아직 기록되지 않았습니다.' : '실행 기록을 연결하고 있습니다.', 'obs-events-empty'));
      replace(eventList, fragment);
    });
    older.hidden = !model.pagination?.has_more; older.disabled = model.pageLoading || !model.connected;
    older.textContent = model.pageLoading ? '이전 기록을 불러오는 중…' : '이전 기록 불러오기';
    eventCount.textContent = `${filtered.length}개 표시 · ${model.events.length}개 불러옴${model.pagination?.has_more ? ' · 이전 기록 있음' : ''}`;
  }
  function render() {
    if (!model.taskId) { root.hidden = true; room?.setVisible(false); return; }
    root.hidden = !model.visible;
    const data = model.data, task = data?.task || model.task;
    taskLine.textContent = `${task?.id || model.taskId} · ${task?.goal || '선택한 작업'}`;
    connection.textContent = model.connected ? `연결됨 · ${timeLabel(model.lastSync)}` : model.error ? '연결 확인 필요 · 이전 기록' : '실행 기록 연결 중';
    connection.className = `obs-connection ${model.connected ? 'is-connected' : model.error ? 'is-disconnected' : ''}`;
    root.classList.toggle('is-offline', !model.connected); root.classList.toggle('is-replaying', model.replay != null);
    reload.disabled = model.fetching;
    const runOptions = data?.runs || model.runOptions;
    cached('runs', [runOptions, data?.selected_run_id, model.requestedRun], () => {
      const fragment = document.createDocumentFragment(), latest = el('option', '현재 실행 따라가기'); latest.value = ''; fragment.append(latest);
      for (const run of runOptions) { const option = el('option', `${dateLabel(run.started_at)} · ${run.action} · ${statusLabel(run.status)} · ${run.id.slice(0, 7)}`); option.value = run.id; fragment.append(option); }
      replace(runSelect, fragment); runSelect.value = model.requestedRun || ''; runSelect.disabled = !runOptions.length;
    });
    exportLink.href = `/api/observability/export?task_id=${encodeURIComponent(model.taskId)}${data?.selected_run_id ? `&run_id=${encodeURIComponent(data.selected_run_id)}` : ''}`;
    exportLink.hidden = !data;
    const warnings = [...(data?.warnings || [])];
    if (model.error) warnings.unshift(`${model.error} 연결을 자동으로 다시 시도합니다. 마지막 기록은 보존하며 활동 표시를 멈춥니다.`);
    if (data?.task?.hold) warnings.unshift(`수동 보류 · ${data.task.hold.reason || '보류 사유가 기록되지 않았습니다.'}`);
    if (model.pagination?.client_limit) warnings.push('브라우저에는 최대 5,000개 이벤트를 표시합니다. 더 많은 기록은 JSON으로 내보내세요.');
    if (data?.runs_truncated) warnings.push('최근 실행 30개를 표시합니다.');
    cached('warnings', warnings, () => { const fragment = document.createDocumentFragment(); for (const warning of warnings) fragment.append(el('p', warning, 'obs-inline-warning')); replace(notices, fragment); });
    const spans = visibleSpans();
    renderOffice(spans);
    const idx = model.replay == null ? model.events.length - 1 : model.events.findIndex(event => Number(event.seq) === model.replay);
    slider.max = String(Math.max(0, model.events.length - 1)); slider.value = String(Math.max(0, idx)); slider.disabled = !model.events.length;
    previous.disabled = model.events.length < 2 || idx <= 0; next.disabled = model.events.length < 2 || idx >= model.events.length - 1;
    play.disabled = !model.events.length; live.disabled = model.replay == null;
    replayLabel.textContent = model.replay == null ? '실시간 기록' : `기록 재생 · #${model.replay}`;
    slider.setAttribute('aria-valuetext', model.replay == null ? '가장 최근 기록' : `이벤트 ${model.replay}, ${timeLabel(model.events[idx]?.at)}`);
    stepsButton.setAttribute('aria-pressed', String(model.mode === 'steps')); rolesButton.setAttribute('aria-pressed', String(model.mode === 'roles'));
    graphHint.textContent = model.mode === 'steps' ? `${spans.length}개 실제 실행 단계 · 수정과 재검사를 각각 표시합니다.` : '같은 역할의 호출을 합친 개요입니다. 정확한 순서는 실행 순서에서 확인하세요.';
    renderGraph(spans);
    renderInspector(); renderEvents();
  }

  async function fetchJSON(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    let value;
    try { value = await response.json(); } catch (_) { throw new Error(`관측 응답을 읽지 못했습니다 (${response.status}).`); }
    if (!response.ok) throw new Error(value.error || value.message || `관측 기록 요청 실패 (${response.status}).`);
    return value;
  }
  function requestURL(before) {
    const params = new URLSearchParams({ task_id: model.taskId, limit: '200' });
    if (model.requestedRun) params.set('run_id', model.requestedRun);
    if (before) params.set('before', String(before));
    return `/api/observability?${params}`;
  }
  function mergeEvents(incoming) {
    const bySequence = new Map(model.events.map(event => [Number(event.seq), event]));
    for (const event of incoming || []) bySequence.set(Number(event.seq), event);
    const all = [...bySequence.values()].sort((a, b) => Number(a.seq) - Number(b.seq));
    if (all.length > 5000) { model.events = all.slice(-5000); if (model.pagination) { model.pagination.client_limit = true; model.pagination.has_more = false; } }
    else model.events = all;
    if (model.replay != null && !model.events.some(event => Number(event.seq) === model.replay)) { stopPlayback(); model.replay = null; model.selectedEvent = null; }
  }
  function schedule() {
    clearTimeout(model.timer);
    if (model.taskId && model.visible && !document.hidden) model.timer = setTimeout(fetchData, Math.min(30000, 2000 * 2 ** model.failures));
  }
  async function fetchData() {
    if (!model.taskId || model.fetching || document.hidden || !model.visible) return;
    clearTimeout(model.timer);
    const generation = model.generation;
    const controller = new AbortController(); model.abort = controller; model.fetching = true;
    const timeout = setTimeout(() => controller.abort(), 10000); reload.disabled = true;
    try {
      const data = await fetchJSON(requestURL(), { signal: controller.signal });
      if (generation !== model.generation) return;
      if (data.schema_version !== 1 || data.task?.id !== model.taskId) throw new Error('지원되지 않는 관측 기록 형식입니다.');
      const runChanged = model.data && model.data.selected_run_id !== data.selected_run_id;
      if (runChanged) { stopPlayback(); model.events = []; model.replay = null; model.selectedSpan = null; model.selectedEvent = null; model.pagination = null; model.logCache.clear(); model.logKind = null; }
      if (!model.pagination || !model.events.length) model.pagination = { ...data.pagination };
      model.data = data; model.runOptions = data.runs || []; mergeEvents(data.events); model.connected = true; model.error = null; model.failures = 0; model.lastSync = data.generated_at || new Date().toISOString();
      if (model.followLatest && model.replay == null && data.spans?.length) { model.selectedSpan = data.spans.at(-1).id; model.selectedEvent = null; }
      render();
    } catch (error) {
      if (generation !== model.generation || document.hidden || !model.visible) return;
      model.connected = false; model.error = error.name === 'AbortError' ? '관측 서버 응답 시간이 초과됐습니다.' : error.message; model.failures = Math.min(4, model.failures + 1); stopPlayback(); render();
    } finally {
      clearTimeout(timeout);
      if (generation === model.generation) { model.fetching = false; model.abort = null; reload.disabled = false; schedule(); }
    }
  }
  async function loadOlder() {
    if (model.pageLoading || !model.pagination?.has_more || !model.pagination.next_before) return;
    const generation = model.generation, run = model.data?.selected_run_id;
    model.pageLoading = true; renderEvents();
    try {
      const params = new URLSearchParams({ task_id: model.taskId, limit: '200', before: String(model.pagination.next_before) });
      if (run) params.set('run_id', run);
      const data = await fetchJSON(`/api/observability?${params}`);
      if (generation !== model.generation || run !== model.data?.selected_run_id) return;
      model.pagination = { ...data.pagination }; mergeEvents(data.events); render();
    } catch (error) { if (generation === model.generation) { model.error = error.message; render(); } }
    finally { if (generation === model.generation) { model.pageLoading = false; renderEvents(); } }
  }
  function resetScope() {
    model.generation += 1; clearTimeout(model.timer); model.abort?.abort(); stopPlayback();
    model.data = null; model.events = []; model.pagination = null; model.runOptions = []; model.selectedSpan = null; model.selectedEvent = null;
    model.replay = null; model.connected = false; model.error = null; model.failures = 0; model.fetching = false; model.pageLoading = false;
    model.logCache.clear(); model.logKind = null; model.followLatest = true; model.renderKeys = {}; model.query = ''; search.value = '';
  }
  function chooseRun(runId) { const options = model.runOptions; resetScope(); model.runOptions = options; model.requestedRun = runId; render(); fetchData(); }
  window.WorkflowObservatory = {
    update(context = {}) {
      const task = context.task || null, taskId = task?.id || null;
      model.task = task; model.detail = context.detail || null;
      if (taskId !== model.taskId) {
        resetScope(); model.taskId = taskId; model.requestedRun = null; render(); if (taskId) fetchData();
      }
    },
    refresh: fetchData,
    setVisible(value) {
      const visible = value === true;
      if (model.visible === visible) return;
      model.visible = visible;
      if (!visible) { clearTimeout(model.timer); model.abort?.abort(); stopPlayback(); model.connected = false; room?.setVisible(false); }
      render();
      if (visible) fetchData();
    }
  };
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(model.timer); model.abort?.abort(); stopPlayback(); office.classList.add('is-paused'); }
    else { office.classList.remove('is-paused'); model.connected = false; fetchData(); }
    if (model.taskId) render();
  });
  window.addEventListener('workflow:preferences', event => {
    const preferences = event.detail || {};
    if (typeof preferences.threeD === 'boolean') { model.preferences.threeD = preferences.threeD; model.officeMode = preferences.threeD ? '3d' : 'list'; }
    if (['system', 'reduce'].includes(preferences.motion)) model.preferences.motion = preferences.motion;
    if (model.taskId) renderOffice(visibleSpans());
  });
  motionQuery.addEventListener('change', () => { if (model.taskId) renderOffice(visibleSpans()); });
  new MutationObserver(() => { if (model.taskId) updateRoom(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  window.addEventListener('pagehide', event => {
    room?.setVisible(false);
    if (!event.persisted) { roomDisposed = true; room?.dispose(); }
  });
  let resizeFrame = null;
  function resizeGraph() {
    if (resizeFrame != null) return;
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = null;
      if (!root.hidden && graph.clientWidth) renderGraph(visibleSpans());
    });
  }
  if (typeof ResizeObserver === 'function') new ResizeObserver(resizeGraph).observe(graph);
  window.addEventListener('resize', resizeGraph, { passive: true });
})();
