# Frontend third-party software

The dashboard at `/`, `/mission`, `/overview` and `/sessions` ships as a local React application. Its pinned runtime includes React 19.3.1, React DOM 19.3.1, React Three Fiber 9.8.2, Three.js 0.187.0 and their runtime dependencies. The complete license texts ship in [MISSION_LICENSES.txt](agent_workflow/static/MISSION_LICENSES.txt).

React Three Fiber's package omits its MIT license file. The build includes the [vendored license](frontend/licenses/react-three-fiber-LICENSE.txt) from the [upstream v9.8.2 release](https://raw.githubusercontent.com/pmndrs/react-three-fiber/v9.8.2/LICENSE). The React reconciler preserves its license header and uses the included React MIT license.

The procedural room, graph, worker geometry and application components in `frontend/mission/` are project code. The browser loads bundled assets from the local controller. Esbuild 0.28.2 (MIT), TypeScript and Prettier are development tools. The release build does not require a Node server.

## Reproducing the shipped assets

```sh
npm ci
npm run typecheck
npm run test:mission
npm run build
npm run check:mission
```

`scripts/build-mission.mjs` builds the JavaScript and CSS, collects runtime licenses from the pinned packages and verifies the committed distribution. `npm run dev` watches the same React source.
