import {
  ACESFilmicToneMapping, Box3, BoxGeometry, BufferGeometry, Color,
  ConeGeometry, CubicBezierCurve3, CylinderGeometry, DirectionalLight,
  Group, HemisphereLight, Line, LineBasicMaterial, Mesh, MeshBasicMaterial,
  MeshStandardMaterial, OrthographicCamera, OrbitControls, PCFShadowMap,
  PMREMGenerator, Raycaster, RoomEnvironment, RoundedBoxGeometry, Scene,
  SphereGeometry, SRGBColorSpace, TorusGeometry, TubeGeometry, Vector2,
  Vector3, WebGLRenderer,
} from './three-vendor.js';

// The scene only receives the observatory's replay-safe view model. It never
// fetches data or changes a workflow. Role chips provide the keyboard pathway.
const ROLE_COLORS = { controller: '#b7d4ee', implement: '#75e2ae', check: '#edc076', review: '#8ebdec', release: '#b7a3e7' };
const ROLE_ORDER = ['controller', 'implement', 'check', 'review', 'release'];
const roleOf = role => ['integration_check', 'deploy', 'health'].includes(role) ? 'release' : ROLE_ORDER.includes(role) ? role : 'controller';
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/**
 * @param {HTMLElement} container an empty, positioned scene host
 * @param {{onSelectActor?:function,onAvailabilityChange?:function}} callbacks
 * @returns {{update:function,setVisible:function,reset:function,zoom:function,dispose:function}}
 */
export function createOffice(container, { onSelectActor = () => {}, onAvailabilityChange = () => {} } = {}) {
  if (!(container instanceof HTMLElement)) throw new TypeError('A scene container is required.');
  let renderer, controls, frame = null, disposed = false, unavailable = false;
  let intersecting = true, announced = false, drawCount = 0, lastAnimation = 0;
  let width = 0, height = 0, actorKey = '', edgeKey = '', pointerStart = null;
  const state = { actors: [], edges: [], transfers: [], scope: null, selectedActor: null, connected: false, replaying: false, visible: true, reducedMotion: false, theme: 'light' };
  const geometries = new Map(), materials = new Map(), resources = new Set();
  const stations = new Map(), targets = [], edgesGroup = new Group(), transfers = new Map(), seenTransfers = new Set();
  let currentScope = null, stationGeneration = 0;
  const scene = new Scene();
  const camera = new OrthographicCamera(-10, 10, 6, -6, .1, 100);
  const raycaster = new Raycaster(), pointer = new Vector2();
  const projection = new Vector3(), lookAt = new Vector3(0, .7, 0);
  const baseDirection = new Vector3(11, 12, 16).normalize();
  const roomBounds = new Box3(new Vector3(-7.35, -.2, -4.95), new Vector3(7.35, 3.0, 4.95));
  const labels = document.createElement('div'); labels.className = 'scene3d-labels'; labels.setAttribute('aria-hidden', 'true');
  container.dataset.sceneStatus = 'loading';
  container.dataset.sceneZoom = '1';
  const coarsePointer = window.matchMedia?.('(pointer: coarse)');

  function fail(reason) {
    if (disposed || unavailable) return;
    unavailable = true;
    if (frame != null) cancelAnimationFrame(frame);
    frame = null;
    container.dataset.sceneStatus = 'unavailable';
    if (controls) controls.enabled = false;
    onAvailabilityChange({ available: false, reason });
  }
  try {
    renderer = new WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'low-power' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.toneMapping = ACESFilmicToneMapping;
    renderer.toneMappingExposure = .90;
    renderer.setClearColor('#202c37', 1);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = PCFShadowMap;
    // Furniture and lighting are static. Subtle typing need not redraw the
    // entire shadow map on every frame, especially on integrated GPUs.
    renderer.shadowMap.autoUpdate = false;
    renderer.shadowMap.needsUpdate = true;
    renderer.domElement.className = 'scene3d-canvas';
    renderer.domElement.dataset.officeCanvas = '';
    renderer.domElement.setAttribute('aria-hidden', 'true');
    container.append(renderer.domElement, labels);
  } catch (_) {
    fail('이 브라우저에서는 3D 작업실을 표시할 수 없습니다. 역할 목록과 실행 그래프를 사용할 수 있습니다.');
    return { update() {}, setVisible() {}, reset() {}, zoom() {}, dispose() { disposed = true; container.dataset.sceneStatus = 'disposed'; } };
  }

  function geometry(key, create) {
    if (!geometries.has(key)) geometries.set(key, create());
    return geometries.get(key);
  }
  function material(color, options = {}) {
    const key = JSON.stringify([color, options]);
    if (!materials.has(key)) materials.set(key, new MeshStandardMaterial({ color, roughness: .76, metalness: .04, ...options }));
    return materials.get(key);
  }
  function ownedMaterial(value) { resources.add(value); return value; }
  function place(parent, geo, mat, x = 0, y = 0, z = 0, cast = true) {
    const object = new Mesh(geo, mat); object.position.set(x, y, z);
    object.castShadow = cast; object.receiveShadow = true; parent.add(object); return object;
  }
  function box(parent, w, h, d, color, x = 0, y = 0, z = 0, radius = .035, options = {}) {
    const key = `box:${w}:${h}:${d}:${radius}`;
    return place(parent, geometry(key, () => radius ? new RoundedBoxGeometry(w, h, d, 2, Math.min(radius, w / 3, h / 3, d / 3)) : new BoxGeometry(w, h, d)), material(color, options), x, y, z);
  }
  function cylinder(parent, top, bottom, h, color, x = 0, y = 0, z = 0, sides = 16, options = {}) {
    const key = `cylinder:${top}:${bottom}:${h}:${sides}`;
    return place(parent, geometry(key, () => new CylinderGeometry(top, bottom, h, sides)), material(color, options), x, y, z);
  }
  function sphere(parent, radius, color, x = 0, y = 0, z = 0, options = {}) {
    return place(parent, geometry(`sphere:${radius}`, () => new SphereGeometry(radius, 18, 12)), material(color, options), x, y, z);
  }
  function tube(parent, points, mat, radius = .025) {
    const curve = new CubicBezierCurve3(...points);
    const geo = new TubeGeometry(curve, 28, radius, 5, false); resources.add(geo);
    return place(parent, geo, mat, 0, 0, 0, false);
  }
  function plant(parent, x, z, scale = 1) {
    const pot = new Group(); pot.position.set(x, .44, z); pot.scale.setScalar(scale); parent.add(pot);
    cylinder(pot, .19, .145, .34, '#8b8b7b', 0, .17, 0);
    cylinder(pot, .17, .17, .024, '#4a4837', 0, .345, 0);
    cylinder(pot, .018, .024, .67, '#626b39', 0, .65, 0, 7);
    for (let i = 0; i < 9; i++) {
      const angle = i * 2.4, elevation = .55 + (i % 4) * .13;
      const leaf = sphere(pot, .13, ['#68895a', '#7a985d', '#52764e'][i % 3], Math.cos(angle) * .16, elevation, Math.sin(angle) * .16);
      leaf.scale.set(.56, 1.9, .32); leaf.rotation.set(Math.sin(angle) * .6, angle, Math.cos(angle) * .85);
    }
    return pot;
  }

  // Warm, rounded architecture and recognisable furniture, all procedural.
  box(scene, 14.1, .42, 9.45, '#5f6d77', 0, .04, 0, .2);
  box(scene, 13.85, .18, 9.2, '#aa9b83', 0, .31, 0, .18);
  box(scene, 13.55, .05, 8.9, '#a58a67', 0, .425, 0, .12);
  const floorLineMat = ownedMaterial(new LineBasicMaterial({ color: '#8f8576', transparent: true, opacity: .28 }));
  for (let x = -6; x <= 6; x += 1.2) {
    const geo = new BufferGeometry().setFromPoints([new Vector3(x, .456, -4.35), new Vector3(x, .456, 4.35)]); resources.add(geo); scene.add(new Line(geo, floorLineMat));
  }
  for (let row = 0; row < 5; row++) {
    for (let column = 0; column < 6; column++) {
      const x = -5.9 + column * 2.4 + (row % 2 ? 1.2 : 0), z = -3.8 + row * 1.8;
      if (x > 6.2) continue;
      const geo = new BufferGeometry().setFromPoints([new Vector3(x, .456, z), new Vector3(Math.min(6.7, x + 1.2), .456, z)]); resources.add(geo); scene.add(new Line(geo, floorLineMat));
    }
  }
  box(scene, 13.25, 1.3, .14, '#96948a', 0, 1.10, -4.36, .07);
  box(scene, .14, 1.3, 3.45, '#a39a8a', -6.64, 1.10, -2.70, .07);
  for (const x of [-4.42, -2.2, 0, 2.2, 4.42]) box(scene, .035, 1.15, .045, '#d2c9b9', x, 1.13, -4.265, .01);
  box(scene, 1.25, 1.18, .39, '#8e806c', -2.2, 1.04, -4.04, .035);
  for (let shelf = 0; shelf < 2; shelf++) {
    box(scene, 1.16, .04, .41, '#c7b99f', -2.2, .78 + shelf * .53, -3.99, .01);
    for (let i = 0; i < 7; i++) box(scene, .10, .26 + (i % 3) * .04, .18, ['#627d76', '#a9916f', '#879aab', '#c9b999'][i % 4], -2.66 + i * .14, .95 + shelf * .53, -3.86, .012);
  }
  plant(scene, -6.12, -3.8, 1.05); plant(scene, 6.0, -3.78, 1.12);
  plant(scene, -6.1, 2.85, 1.05); plant(scene, 6.1, 2.8, .95); plant(scene, 0, 3.75, .85);
  scene.add(edgesGroup);

  // Keep wood, fabric and white shells distinguishable on small displays.
  // A broad fill supports details while the warm key supplies depth.
  const hemisphere = new HemisphereLight('#dbeaff', '#bba98e', .78); scene.add(hemisphere);
  const sun = new DirectionalLight('#fff0d7', 1.85); sun.position.set(-5, 11, 8); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.camera.left = -10; sun.shadow.camera.right = 10;
  sun.shadow.camera.top = 10; sun.shadow.camera.bottom = -10; sun.shadow.camera.near = 1; sun.shadow.camera.far = 35;
  sun.shadow.bias = -.0003; sun.shadow.normalBias = .025; sun.shadow.radius = 3; scene.add(sun);
  const fill = new DirectionalLight('#b8d9ed', .42); fill.position.set(7, 6, -6); scene.add(fill);
  try {
    const environment = new RoomEnvironment();
    const generator = new PMREMGenerator(renderer);
    const target = generator.fromScene(environment, .04);
    scene.environment = target.texture;
    scene.environmentIntensity = .35;
    resources.add(target); environment.dispose(); generator.dispose();
  } catch (_) { /* Direct and hemisphere lighting remain sufficient. */ }

  function roundedOutline(parent, w, d, mat) {
    const points = [], radius = .3;
    for (const [cx, cz, start] of [[w / 2 - radius, d / 2 - radius, 0], [-w / 2 + radius, d / 2 - radius, Math.PI / 2], [-w / 2 + radius, -d / 2 + radius, Math.PI], [w / 2 - radius, -d / 2 + radius, Math.PI * 1.5]]) {
      for (let i = 0; i <= 8; i++) {
        const angle = start + i / 8 * Math.PI / 2;
        points.push(new Vector3(cx + Math.cos(angle) * radius, .485, cz + Math.sin(angle) * radius));
      }
    }
    points.push(points[0]);
    const group = new Group(); parent.add(group);
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i], b = points[i + 1], distance = a.distanceTo(b);
      const line = place(group, geometry(`rod:${distance.toFixed(4)}`, () => new CylinderGeometry(.02, .02, distance, 6)), mat, 0, 0, 0, false);
      line.position.copy(a).add(b).multiplyScalar(.5); line.quaternion.setFromUnitVectors(new Vector3(0, 1, 0), b.clone().sub(a).normalize());
    }
    return group;
  }
  function makeStation(actor, position) {
    const group = new Group(); group.position.set(position.x, 0, position.z); group.rotation.y = position.rotation || 0; scene.add(group);
    const role = roleOf(actor.role), accent = ROLE_COLORS[role];
    box(group, 3.1, .027, 2.8, '#73817b', 0, .47, -.12, .15, { roughness: 1 });
    const ringMat = ownedMaterial(new MeshBasicMaterial({ color: accent, transparent: true, opacity: .18 }));
    roundedOutline(group, 3.15, 2.85, ringMat);
    box(group, 2.62, .13, 1.2, '#b18b60', 0, 1.30, .35, .065, { roughness: .65 });
    box(group, 2.52, .05, 1.1, '#d0ad7c', 0, 1.385, .35, .025);
    box(group, .55, .81, .87, '#a48861', -.91, .87, .33, .045);
    for (const y of [.66, .93, 1.17]) {
      box(group, .50, .012, .02, '#95826b', -.91, y, .78, .004);
      box(group, .13, .025, .045, '#727f7a', -.91, y + .09, .81, .008);
    }
    for (const z of [-.02, .80]) cylinder(group, .042, .042, .81, '#576466', 1.05, .87, z, 10);
    box(group, .40, .09, .22, '#697777', .22, 1.46, .64, .025);
    box(group, .065, .34, .07, '#667677', .22, 1.63, .64, .015);
    const monitor = new Group(); monitor.position.set(.2, 1.90, .64); monitor.rotation.y = -.12; group.add(monitor);
    box(monitor, 1.02, .63, .075, '#40525a', 0, 0, 0, .04, { roughness: .55 });
    box(monitor, .91, .51, .012, '#182a31', 0, 0, -.045, .02, { roughness: .38 });
    // A small maker mark on the monitor back; actual output lives in the inspector.
    box(monitor, .18, .055, .015, '#708e95', 0, -.01, .045, .02);
    const screenMat = ownedMaterial(new MeshStandardMaterial({ color: '#67c6ad', emissive: '#67c6ad', emissiveIntensity: .25, roughness: .7 }));
    for (let i = 0; i < 4; i++) {
      const strip = place(monitor, geometry(`screenstrip:${i}`, () => new BoxGeometry(.34 + (i % 3) * .12, .015, .008)), screenMat, -.12 + (i % 2) * .05, .14 - i * .09, -.054, false);
      strip.receiveShadow = false;
    }
    box(group, .65, .035, .24, '#7d8985', .14, 1.44, .11, .025);
    for (let row = 0; row < 3; row++) for (let col = 0; col < 8; col++) box(group, .055, .015, .04, '#ced4c9', -.13 + col * .077, 1.465, .035 + row * .065, .009);
    box(group, .105, .045, .16, '#d1d8ce', .65, 1.46, .12, .035);
    box(group, .29, .035, .36, '#ddd3b5', .95, 1.435, .27, .01);
    cylinder(group, .075, .065, .15, '#e8e1d1', -.90, 1.485, .10, 16);
    const handle = place(group, geometry('mug-handle', () => new TorusGeometry(.045, .012, 6, 12)), material('#e8e1d1'), -.81, 1.50, .10); handle.rotation.y = Math.PI / 2;
    cylinder(group, .09, .12, .03, '#8e876d', -1.01, 1.44, .72, 16);
    cylinder(group, .013, .013, .40, '#898268', -1.01, 1.65, .72, 8);
    cylinder(group, .09, .13, .15, '#d4c6a7', -1.01, 1.88, .72, 16);
    cylinder(group, .095, .095, .014, '#ffdfa0', -1.01, 1.805, .72, 16, { emissive: '#ffd98a', emissiveIntensity: .65 });

    // Chair with upholstered seat, back, central lift and five castors.
    cylinder(group, .05, .07, .35, '#687572', 0, .66, -.68, 10);
    for (let i = 0; i < 5; i++) {
      const angle = i * Math.PI * .4;
      const foot = box(group, .43, .045, .05, '#687572', Math.cos(angle) * .15, .51, -.68 + Math.sin(angle) * .15, .02); foot.rotation.y = -angle;
      sphere(group, .046, '#48544f', Math.cos(angle) * .34, .50, -.68 + Math.sin(angle) * .34);
    }
    box(group, .57, .13, .54, '#82958b', 0, .97, -.68, .07, { roughness: 1 });
    box(group, .58, .64, .12, '#82958b', 0, 1.31, -.97, .06, { roughness: 1 });
    for (const x of [-.35, .35]) box(group, .07, .08, .34, '#53665b', x, 1.18, -.65, .035);
    const robot = new Group(); robot.position.set(0, 0, -.66); group.add(robot);
    const body = sphere(robot, .25, '#e1e8df', 0, 1.25, 0, { roughness: .35, metalness: .1 }); body.scale.set(.83, 1.05, .68);
    const collar = cylinder(robot, .13, .15, .07, accent, 0, 1.49, 0, 16, { roughness: .5 });
    const head = new Group(); head.position.set(0, 1.80, 0); robot.add(head);
    const shell = sphere(head, .31, '#edf1e8', 0, 0, 0, { roughness: .27, metalness: .12 }); shell.scale.set(1, .94, .84);
    box(head, .44, .26, .07, '#102325', 0, -.015, .238, .09, { roughness: .23, metalness: .15 });
    const eyeMat = ownedMaterial(new MeshStandardMaterial({ color: accent, emissive: accent, emissiveIntensity: .45, roughness: .4 }));
    for (const x of [-.105, .105]) {
      const eye = place(head, geometry('robot-eye', () => new RoundedBoxGeometry(.042, .075, .014, 2, .018)), eyeMat, x, -.008, .278, false);
      eye.receiveShadow = false;
    }
    for (const x of [-.31, .31]) {
      const ear = cylinder(head, .075, .075, .055, accent, x, -.005, 0, 14, { metalness: .18, roughness: .4 }); ear.rotation.z = Math.PI / 2;
    }
    const hands = [];
    for (const x of [-.22, .22]) {
      const arm = new Group(); arm.position.set(x, 1.37, .02); arm.rotation.x = -.7; robot.add(arm);
      const upper = sphere(arm, .095, '#dce5dc', 0, -.08, .06, { roughness: .38 }); upper.scale.set(.8, 1.4, .8);
      const fore = sphere(robot, .07, '#edf0e6', x, 1.48, .45, { roughness: .4 }); fore.scale.set(1, .7, 1.15); hands.push(fore);
      const leg = sphere(robot, .085, '#d8e2d9', x * .54, 1.02, .19); leg.scale.set(.85, 1.6, .9);
      box(robot, .13, .10, .25, '#7f9290', x * .54, .84, .24, .04);
    }
    box(group, 2.72, .58, .065, '#a6a79c', 0, .99, -1.42, .03);
    box(group, 2.72, .035, .09, '#ccc4b1', 0, 1.30, -1.42, .015);
    group.traverse(object => { if (object.isMesh) { object.userData.actorId = actor.id; targets.push(object); } });
    const label = document.createElement('div'); label.className = 'scene3d-label'; label.dataset.sceneActor = actor.id;
    label.style.setProperty('--scene-agent-color', accent);
    const labelText = document.createElement('span'); labelText.className = 'scene3d-label-name';
    const labelStatus = document.createElement('span'); labelStatus.className = 'scene3d-label-status';
    label.append(labelText, labelStatus); labels.append(label); label.dataset.stationGeneration = String(++stationGeneration);
    return { actor, group, robot, head, hands, ringMat, eyeMat, screenMat, label, labelText, labelStatus, transition: null, arrivedAt: null, completedAt: null, anchor: new Vector3(0, 1.80, -.48), baseHeadY: head.position.y, accent, collar };
  }
  function layoutActors(actors) {
    const ordered = [...actors].sort((a, b) => ROLE_ORDER.indexOf(roleOf(a.role)) - ROLE_ORDER.indexOf(roleOf(b.role)) || a.id.localeCompare(b.id));
    if (ordered.length <= 5) {
      const spots = {
        controller: { x: -4.30, z: -1.75, rotation: .08 },
        implement: { x: 0, z: -2.1, rotation: 0 },
        check: { x: 4.30, z: -1.75, rotation: -.08 },
        review: { x: -2.55, z: 2.0, rotation: .12 },
        release: { x: 2.55, z: 2.0, rotation: -.12 },
      };
      const occupied = new Set();
      if (ordered.every(actor => { const role = roleOf(actor.role); if (occupied.has(role)) return false; occupied.add(role); return true; })) return ordered.map(actor => [actor, spots[roleOf(actor.role)]]);
    }
    const columns = Math.min(4, Math.max(1, Math.ceil(ordered.length / 2))), rows = Math.ceil(ordered.length / columns);
    const scale = rows > 2 ? .72 : .88;
    return ordered.map((actor, index) => [actor, { x: (index % columns - (columns - 1) / 2) * 3.25, z: (Math.floor(index / columns) - (rows - 1) / 2) * (rows > 2 ? 2.6 : 3.8), rotation: 0, scale }]);
  }
  function removeStation(station) {
    scene.remove(station.group); station.label.remove();
    for (const mat of [station.ringMat, station.eyeMat, station.screenMat]) { mat.dispose(); resources.delete(mat); }
  }
  function removeStations() {
    for (const station of stations.values()) removeStation(station);
    stations.clear(); targets.length = 0;
  }
  function motionAllowed() { return canDraw() && !state.reducedMotion && state.connected && !state.replaying; }
  function restPose(station) {
    station.head.rotation.set(0, 0, 0); station.head.position.y = station.baseHeadY;
    station.robot.position.z = -.66;
    for (const hand of station.hands) hand.position.y = 1.48;
  }
  function updateActors() {
    const nextKey = JSON.stringify(state.actors.map(actor => [actor.id, actor.role]));
    const layoutChanged = nextKey !== actorKey, hadStations = stations.size > 0;
    if (layoutChanged) {
      const ids = new Set(state.actors.map(actor => actor.id));
      for (const [id, station] of stations) if (!ids.has(id)) { removeStation(station); stations.delete(id); }
      for (const [actor, position] of layoutActors(state.actors)) {
        let station = stations.get(actor.id);
        if (!station) {
          station = makeStation(actor, position); stations.set(actor.id, station);
          if (hadStations && actor.active && motionAllowed()) station.arrivedAt = performance.now();
        }
        const target = new Vector3(position.x, 0, position.z);
        if (!station.group.position.equals(target) && motionAllowed()) {
          station.transition = { from: station.group.position.clone(), to: target, started: performance.now() };
        } else { station.group.position.copy(target); station.transition = null; }
        station.group.scale.setScalar(position.scale || 1); station.group.rotation.y = position.rotation || 0;
      }
      targets.length = 0;
      for (const station of stations.values()) station.group.traverse(object => { if (object.isMesh) targets.push(object); });
      renderer.shadowMap.needsUpdate = true;
      actorKey = nextKey; edgeKey = '';
    }
    for (const actor of state.actors) {
      const station = stations.get(actor.id); if (!station) continue;
      if (station.actor.status === 'running' && ['succeeded', 'pass', 'ready'].includes(actor.status) && motionAllowed()) station.completedAt = performance.now();
      station.actor = actor;
      const selected = actor.id === state.selectedActor || actor.selected === true;
      const active = actor.active === true && actor.status === 'running' && state.connected && !state.replaying;
      const warning = ['blocked', 'changes_requested', 'failed', 'interrupted', 'unknown', 'held'].includes(actor.status);
      const color = warning ? '#edb668' : station.accent;
      station.ringMat.color.set(selected || active ? color : '#a8b1a7');
      station.ringMat.opacity = selected ? .95 : active ? .8 : .08;
      station.eyeMat.color.set(color); station.eyeMat.emissive.set(color); station.eyeMat.emissiveIntensity = active ? .8 : .25;
      station.screenMat.emissiveIntensity = active ? .5 : .12;
      station.labelText.textContent = actor.label || actor.id;
      station.labelStatus.textContent = !state.connected ? '이전 기록' : state.replaying && actor.status === 'running' ? '당시 실행 중' : actor.statusLabel || actor.status;
      station.label.title = [actor.label, actor.goal, actor.taskId].filter(Boolean).join(' · ');
      station.label.classList.toggle('is-selected', selected); station.label.classList.toggle('is-active', active);
      station.label.classList.toggle('is-unavailable', !state.connected || actor.status === 'unknown');
      if (!motionAllowed()) {
        if (station.transition) station.group.position.copy(station.transition.to);
        station.transition = null; station.arrivedAt = null; station.completedAt = null;
      }
      if (!active || !motionAllowed()) restPose(station);
    }
  }
  function clearTransfers() {
    for (const transfer of transfers.values()) scene.remove(transfer.mesh);
    transfers.clear();
  }
  function updateTransfers() {
    for (const event of state.transfers || []) {
      if (!event?.id || seenTransfers.has(event.id)) continue;
      seenTransfers.add(event.id);
      if (seenTransfers.size > 512) seenTransfers.delete(seenTransfers.values().next().value);
      const source = stations.get(event.source), target = stations.get(event.target);
      if (!source || !target || source === target || !motionAllowed()) continue;
      const from = source.group.position.clone().setY(1.1), to = target.group.position.clone().setY(1.1);
      const curve = new CubicBezierCurve3(from, from.clone().setY(2.8), to.clone().setY(2.8), to);
      const mesh = box(scene, .24, .16, .08, event.kind === 'process_started' ? '#a8d5ff' : '#91efbb', from.x, from.y, from.z, .025, { emissive: '#4e8b77', emissiveIntensity: .6 });
      transfers.set(event.id, { mesh, curve, started: performance.now() });
      if (event.kind === 'process_started') target.arrivedAt = performance.now();
      container.dataset.transferCount = String(Number(container.dataset.transferCount || 0) + 1);
    }
    if (!motionAllowed()) clearTransfers();
  }
  function updateEdges() {
    const edges = state.edges.filter(edge => stations.has(edge.source) && stations.has(edge.target) && edge.source !== edge.target);
    const key = JSON.stringify([actorKey, edges.map(edge => [edge.source, edge.target, edge.kind]), state.selectedActor]);
    if (key === edgeKey) return; edgeKey = key;
    for (const object of [...edgesGroup.children]) {
      edgesGroup.remove(object); object.geometry?.dispose(); if (object.geometry) resources.delete(object.geometry);
      object.material?.dispose(); if (object.material) resources.delete(object.material);
    }
    const seen = new Set();
    for (const edge of edges) {
      const identity = edge.source + ':' + edge.target; if (seen.has(identity)) continue; seen.add(identity);
      const source = stations.get(edge.source).group.position, target = stations.get(edge.target).group.position;
      const start = new Vector3(source.x, .49, source.z + 1.36), end = new Vector3(target.x, .49, target.z + 1.36);
      const midpoint = (start.z + end.z) / 2 + .15;
      const selected = [edge.source, edge.target].includes(state.selectedActor);
      const color = selected ? '#c5f6da' : '#e8e0cc';
      const mat = ownedMaterial(new MeshBasicMaterial({ color, transparent: true, opacity: selected ? .65 : .26 }));
      tube(edgesGroup, [start, new Vector3(start.x, .49, midpoint), new Vector3(end.x, .49, midpoint), end], mat, .014);
      const direction = new Vector3(end.x - start.x, 0, end.z - start.z).normalize();
      if (direction.lengthSq()) {
        const arrowGeo = new ConeGeometry(.055, .15, 3); resources.add(arrowGeo);
        const arrowMat = ownedMaterial(new MeshBasicMaterial({ color, transparent: true, opacity: selected ? .8 : .35 }));
        const arrow = place(edgesGroup, arrowGeo, arrowMat, end.x, .50, end.z, false);
        arrow.quaternion.setFromUnitVectors(new Vector3(0, 1, 0), direction);
      }
    }
  }
  function fitCamera(reset = false) {
    const w = Math.round(container.clientWidth), h = Math.round(container.clientHeight);
    if (!w || !h) return false;
    if (w !== width || h !== height) { width = w; height = h; renderer.setSize(w, h, false); }
    if (reset) { camera.position.copy(lookAt).addScaledVector(baseDirection, 30); camera.zoom = 1; controls.target.copy(lookAt); }
    camera.lookAt(controls.target); camera.updateMatrixWorld();
    const inverse = camera.matrixWorldInverse;
    let maxX = 0, maxY = 0;
    for (const x of [roomBounds.min.x, roomBounds.max.x]) for (const y of [roomBounds.min.y, roomBounds.max.y]) for (const z of [roomBounds.min.z, roomBounds.max.z]) {
      projection.set(x, y, z).applyMatrix4(inverse); maxX = Math.max(maxX, Math.abs(projection.x)); maxY = Math.max(maxY, Math.abs(projection.y));
    }
    const aspect = width / height, halfHeight = Math.max(maxY, maxX / aspect) * 1.035;
    camera.left = -halfHeight * aspect; camera.right = halfHeight * aspect; camera.top = halfHeight; camera.bottom = -halfHeight;
    camera.updateProjectionMatrix(); container.dataset.sceneZoom = camera.zoom.toFixed(3); return true;
  }
  function projectLabels() {
    const placed = [];
    const projected = [...stations.values()].map(station => {
      projection.copy(station.anchor); station.group.localToWorld(projection); projection.project(camera);
      return { station, x: (projection.x + 1) * width / 2, y: (1 - projection.y) * height / 2, z: projection.z };
    }).sort((a,b) => a.y - b.y || a.x - b.x);
    for (const {station,x,y,z} of projected) {
      station.label.dataset.anchorX = x.toFixed(1); station.label.dataset.anchorY = y.toFixed(1);
      station.label.hidden = z < -1 || z > 1 || x < 0 || x > width || y < 0 || y > height;
      if (station.label.hidden) continue;
      const w = station.label.offsetWidth, h = station.label.offsetHeight;
      const left = clamp(x - w / 2, 4, Math.max(4, width - w - 4));
      let top = clamp(y - 18 - h, 4, Math.max(4, height - h - 4));
      for (let attempt = 0; attempt < 24; attempt++) {
        const overlap = placed.some(rect => left < rect.right + 4 && left + w > rect.left - 4 && top < rect.bottom + 4 && top + h > rect.top - 4);
        if (!overlap) break;
        const offset = (Math.floor(attempt / 2) + 1) * (h + 5) * (attempt % 2 ? 1 : -1);
        top = clamp(y - 18 - h + offset, 4, Math.max(4, height - h - 4));
      }
      placed.push({left,right:left+w,top,bottom:top+h});
      station.label.style.left = `${left + w / 2}px`; station.label.style.top = `${top + h}px`;
    }
  }
  function canDraw() { return !disposed && !unavailable && state.visible && intersecting && !document.hidden; }
  function animated() { return motionAllowed() && (transfers.size > 0 || [...stations.values()].some(station => station.transition || station.arrivedAt != null || station.completedAt != null || station.actor.active && station.actor.status === 'running')); }
  function draw(time = 0) {
    frame = null;
    if (!canDraw() || !fitCamera()) return;
    const animate = animated();
    if (animate && time - lastAnimation < 32) { frame = requestAnimationFrame(draw); return; }
    lastAnimation = time;
    if (animate) {
      let index = 0, layoutMoving = false;
      for (const station of stations.values()) {
        if (station.transition) {
          const progress = clamp((time - station.transition.started) / 600, 0, 1), eased = progress * progress * (3 - 2 * progress);
          station.group.position.lerpVectors(station.transition.from, station.transition.to, eased);
          layoutMoving = true;
          if (progress >= 1) station.transition = null;
        }
        if (station.arrivedAt != null) {
          const progress = clamp((time - station.arrivedAt) / 650, 0, 1);
          station.robot.position.z = -.66 - Math.pow(1 - progress, 3) * .75;
          if (progress >= 1) station.arrivedAt = null;
        }
        if (station.completedAt != null) {
          const progress = clamp((time - station.completedAt) / 700, 0, 1);
          station.ringMat.opacity = .15 + Math.sin(progress * Math.PI) * .7;
          if (progress >= 1) { station.completedAt = null; station.ringMat.opacity = station.actor.selected ? .95 : .08; }
        }
        if (!station.actor.active || station.actor.status !== 'running') continue;
        const t = time * .003 + index++;
        station.head.rotation.z = Math.sin(t) * .04;
        station.head.rotation.y = Math.sin(t * .6) * .10;
        station.head.position.y = station.baseHeadY + Math.sin(t * .7) * .018;
        station.hands.forEach((hand, i) => { hand.position.y = 1.48 + Math.sin(t * 3 + i * Math.PI) * .045; });
        station.eyeMat.emissiveIntensity = .7 + Math.sin(t * .8) * .12;
        station.screenMat.emissiveIntensity = .4 + Math.sin(t * 1.2) * .15;
      }
      for (const [id, transfer] of transfers) {
        const progress = clamp((time - transfer.started) / 750, 0, 1);
        transfer.mesh.position.copy(transfer.curve.getPoint(progress)); transfer.mesh.rotation.y = progress * Math.PI;
        if (progress >= 1) { scene.remove(transfer.mesh); transfers.delete(id); }
      }
      if (layoutMoving) { edgeKey = ''; updateEdges(); renderer.shadowMap.needsUpdate = true; }

    }
    try {
      renderer.render(scene, camera); projectLabels(); container.dataset.renderCount = String(++drawCount);
      if (!announced) { announced = true; container.dataset.sceneStatus = 'ready'; onAvailabilityChange({ available: true, reason: null }); }
    } catch (_) { fail('3D 화면을 그리는 중 오류가 발생했습니다. 역할 목록과 실행 그래프를 사용할 수 있습니다.'); return; }
    if (animated()) frame = requestAnimationFrame(draw);
  }
  function invalidate() { if (canDraw() && frame == null) frame = requestAnimationFrame(draw); }
  function stop() { if (frame != null) cancelAnimationFrame(frame); frame = null; }
  function reset() { if (disposed || unavailable) return; fitCamera(true); controls.update(); invalidate(); }
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = false; controls.enablePan = false; controls.enableZoom = false;
  controls.minPolarAngle = Math.PI / 5; controls.maxPolarAngle = Math.PI / 3.1;
  controls.minAzimuthAngle = -.05; controls.maxAzimuthAngle = Math.PI / 2.05;
  controls.rotateSpeed = .45; controls.autoRotate = false;
  controls.enabled = !coarsePointer?.matches;
  renderer.domElement.style.touchAction = 'pan-y';
  controls.addEventListener('change', invalidate);
  camera.position.copy(lookAt).addScaledVector(baseDirection, 30); controls.target.copy(lookAt); controls.update();
  function onDown(event) { if (event.isPrimary !== false && event.button === 0) pointerStart = { x: event.clientX, y: event.clientY, id: event.pointerId }; }
  function onUp(event) {
    if (!pointerStart || pointerStart.id !== event.pointerId) return;
    const distance = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y); pointerStart = null;
    if (distance > 6 || !canDraw()) return;
    const bounds = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - bounds.left) / bounds.width * 2 - 1, -(event.clientY - bounds.top) / bounds.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(targets, false)[0];
    if (hit?.object.userData.actorId) onSelectActor(hit.object.userData.actorId);
  }
  function onCancel() { pointerStart = null; }
  function onContextLost(event) { event.preventDefault(); fail('3D 그래픽 연결이 중단되었습니다. 역할 목록과 실행 그래프로 계속 확인할 수 있습니다.'); }
  function onVisibility() { if (document.hidden) stop(); else invalidate(); }
  function onPointerMode() { controls.enabled = !coarsePointer?.matches && !unavailable; renderer.domElement.style.touchAction = 'pan-y'; }
  renderer.domElement.addEventListener('pointerdown', onDown);
  renderer.domElement.addEventListener('pointerup', onUp);
  renderer.domElement.addEventListener('pointercancel', onCancel);
  renderer.domElement.addEventListener('webglcontextlost', onContextLost);
  document.addEventListener('visibilitychange', onVisibility);
  coarsePointer?.addEventListener?.('change', onPointerMode);
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(invalidate) : null; resizeObserver?.observe(container);
  const intersectionObserver = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
    intersecting = entries[0]?.isIntersecting !== false; if (intersecting) invalidate(); else stop();
  }, { threshold: 0 }) : null; intersectionObserver?.observe(container);
  window.addEventListener('resize', invalidate, { passive: true });
  invalidate();
  return {
    update(next = {}) {
      if (disposed || unavailable) return;
      Object.assign(state, next);
      if (currentScope !== state.scope) { currentScope = state.scope; removeStations(); clearTransfers(); seenTransfers.clear(); actorKey = ''; edgeKey = ''; }
      state.actors = Array.isArray(state.actors) ? state.actors.filter(actor => actor && typeof actor.id === 'string') : [];
      state.edges = Array.isArray(state.edges) ? state.edges.filter(edge => edge && typeof edge.source === 'string' && typeof edge.target === 'string') : [];
      renderer.setClearColor(state.theme === 'dark' ? '#17232e' : '#25333f', 1);
      updateActors(); updateEdges(); updateTransfers();
      if (!canDraw()) stop(); else invalidate();
    },
    setVisible(visible) { state.visible = Boolean(visible); if (canDraw()) invalidate(); else { stop(); clearTransfers(); } },
    reset,
    zoom(delta) {
      if (disposed || unavailable || !Number.isFinite(Number(delta))) return;
      camera.zoom = clamp(camera.zoom * Math.pow(1.18, Number(delta)), .8, 1.8); camera.updateProjectionMatrix();
      container.dataset.sceneZoom = camera.zoom.toFixed(3); invalidate();
    },
    dispose() {
      if (disposed) return; disposed = true; stop();
      resizeObserver?.disconnect(); intersectionObserver?.disconnect();
      controls.removeEventListener('change', invalidate); controls.dispose();
      renderer.domElement.removeEventListener('pointerdown', onDown); renderer.domElement.removeEventListener('pointerup', onUp);
      renderer.domElement.removeEventListener('pointercancel', onCancel); renderer.domElement.removeEventListener('webglcontextlost', onContextLost);
      document.removeEventListener('visibilitychange', onVisibility); coarsePointer?.removeEventListener?.('change', onPointerMode);
      window.removeEventListener('resize', invalidate);
      removeStations(); clearTransfers();
      for (const value of geometries.values()) value.dispose();
      for (const value of materials.values()) value.dispose();
      for (const value of resources) value.dispose?.();
      sun.shadow.dispose(); renderer.dispose(); renderer.forceContextLoss();
      renderer.domElement.remove(); labels.remove(); container.dataset.sceneStatus = 'disposed';
    },
  };
}
