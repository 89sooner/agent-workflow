import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Overview from "./Overview";
import NativeSessions from "./NativeSessions";
import { ConsoleShell } from "./Preferences";
import "./mission.css";
import "./console.css";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConsoleShell>
      {location.pathname === "/overview" ? (
        <Overview />
      ) : location.pathname === "/sessions" ? (
        <NativeSessions />
      ) : (
        <App />
      )}
    </ConsoleShell>
  </StrictMode>,
);
