import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Overview from "./Overview";
import NativeSessions from "./NativeSessions";
import "./mission.css";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <nav className="panel connect-entry"><a href="/overview">프로젝트 통합 관제실</a></nav>
    {location.pathname === "/overview" ? <Overview /> : location.pathname === "/sessions" ? <NativeSessions /> : <App />}
  </StrictMode>,
);
