import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import NativeSessions from "./NativeSessions";
import "./mission.css";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {location.pathname === "/sessions" ? <NativeSessions /> : <App />}
  </StrictMode>,
);
