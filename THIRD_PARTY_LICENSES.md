# Frontend third-party software

## Three.js 0.187.0

- Source: https://github.com/mrdoob/three.js
- Official package: https://www.npmjs.com/package/three/v/0.187.0
- License: MIT; the complete upstream license ships in `agent_workflow/static/THREE_LICENSE.txt`.
- Included modules: the WebGL scene primitives selected in `frontend/vendor-entry.js`, OrbitControls, RoundedBoxGeometry, and RoomEnvironment.
- Distribution: a pinned, locally served ES module at `agent_workflow/static/three-vendor.js`. The application makes no request to a CDN or third-party asset server for the room.

The room geometry, robots, furniture, layout, lighting, state mapping and DOM labels in `scene3d.js` are project code. They do not contain downloaded character models, office textures, fonts or artwork from Pixel Agents or other office visualizers. The bundled RoomEnvironment generates lighting from Three.js's procedural scene.

## esbuild 0.28.2

- Source: https://github.com/evanw/esbuild
- Official package: https://www.npmjs.com/package/esbuild/v/0.28.2
- License: MIT.
- Use: development-time bundling only. esbuild and Node.js are not required to run the installed Python package and dashboard.

## Reproducing the browser bundle

```sh
npm ci --ignore-scripts
npm run build
npm run check:vendor
```

Exact versions and package integrity values are committed in `package-lock.json`. `npm run build` copies the Three.js license from the pinned package and creates a deterministic ES module. `npm run check:vendor` compares a newly built module to the committed distribution. `npm run dev` watches the vendor entry; start the existing Python server separately.

## Runtime behavior

The room uses WebGL 2, with an accessible role list and execution graph provided by the surrounding application when 3D is unavailable. The scene caps device pixel ratio at 1.75, renders live movement at approximately 30 frames per second, and stops its continuous animation loop while idle, hidden, disconnected, replaying, or when reduced motion is enabled. Local controls can request a single render while otherwise idle.

Three.js official API references used for integration: [WebGLRenderer](https://threejs.org/docs/pages/WebGLRenderer.html), [OrbitControls](https://threejs.org/docs/pages/OrbitControls.html), and [RoundedBoxGeometry](https://threejs.org/docs/pages/RoundedBoxGeometry.html).


## React mission control (/mission)

The TypeScript source in `frontend/mission/` builds into local `mission.js` and
`mission.css` using `scripts/build-mission.mjs`. React 19.3.1, React DOM 19.3.1,
React Three Fiber 9.8.2 and Three.js 0.187.0 are pinned in `package-lock.json`.
The complete MIT runtime licenses (including R3F's bundled React reconciler) ship
in `agent_workflow/static/MISSION_LICENSES.txt`. R3F's npm distribution omits its
license file; the checked-in `frontend/licenses/react-three-fiber-LICENSE.txt` is
copied unchanged from https://raw.githubusercontent.com/pmndrs/react-three-fiber/v9.8.2/LICENSE.
The React reconciler retains Meta's license header and uses the included React MIT license.
TypeScript and Prettier are development tools only. No external fonts, models,
textures, CDNs, Node server or model services are needed to display the page.

```sh
npm ci
npm run typecheck
npm run test:mission
npm run build
npm run check:vendor
npm run check:mission
```

`npm run dev:mission` watches the React source. The scene uses R3F's demand loop,
stops while hidden, caps DPR at 1.5, and disables work/transfer animation for
replay, stale data and reduced-motion preferences. Static SVG and DOM views
remain usable when WebGL is unavailable.
