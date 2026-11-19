import {
  createContext,
  useContext,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import { useReducedMotion } from "./api";

type Theme = "light" | "dark";
function read(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Private browsing can disable storage. */
  }
}
function oldPreferences() {
  try {
    return JSON.parse(read("agent-workflow-preferences") || "{}") || {};
  } catch {
    return {};
  }
}
interface Preferences {
  room: boolean;
  reduce: boolean;
  setRoom: (enabled: boolean) => void;
  setReduce: (enabled: boolean) => void;
}
const Context = createContext<Preferences | null>(null);
export function useConsolePreferences() {
  const value = useContext(Context);
  if (!value) throw new Error("Console preferences provider is missing");
  return value;
}

export function ConsoleShell({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() =>
    read("agent-workflow-theme") === "light" ? "light" : "dark",
  );
  const [room, updateRoom] = useState(() =>
    read("mission-room")
      ? read("mission-room") !== "off"
      : oldPreferences().threeD !== false,
  );
  const [reduce, updateReduce] = useState(
    () => (read("mission-motion") || oldPreferences().motion) === "reduce",
  );
  const systemReduce = useReducedMotion();
  const setRoom = (on: boolean) => {
    updateRoom(on);
    store("mission-room", on ? "on" : "off");
  };
  const setReduce = (on: boolean) => {
    updateReduce(on);
    store("mission-motion", on ? "reduce" : "system");
  };
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.motion =
      reduce || systemReduce ? "reduce" : "system";
    store("agent-workflow-theme", theme);
  }, [theme, reduce, systemReduce]);
  return (
    <Context.Provider value={{ room, reduce, setRoom, setReduce }}>
      <nav className="console-nav" aria-label="워크스페이스">
        <a href="/">실행 관제실</a>
        <a href="/overview">프로젝트 통합 관제실</a>
        <a href="/sessions">일상 세션</a>
        <details className="console-preferences">
          <summary>화면 설정</summary>
          <div>
            <label>
              화면 테마{" "}
              <select
                value={theme}
                onChange={(e) => setTheme(e.target.value as Theme)}
              >
                <option value="dark">다크</option>
                <option value="light">라이트</option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={room}
                onChange={(e) => setRoom(e.target.checked)}
              />
              3D 공간 표시
            </label>
            <label>
              <input
                type="checkbox"
                checked={reduce || systemReduce}
                disabled={systemReduce}
                onChange={(e) => setReduce(e.target.checked)}
              />
              움직임 줄이기
            </label>
          </div>
        </details>
      </nav>
      {children}
    </Context.Provider>
  );
}
