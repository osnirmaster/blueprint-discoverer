import "@cloudscape-design/global-styles/index.css";
import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </React.StrictMode>,
);

// minimal styling for wiki markdown tables (Cloudscape has no markdown renderer)
const style = document.createElement("style");
style.textContent = `.bd-md-table{border-collapse:collapse;margin:8px 0;font-size:13px}
.bd-md-table th,.bd-md-table td{border:1px solid var(--color-border-divider-default-cx07f2,#d1d5db);padding:4px 8px;text-align:left}`;
document.head.appendChild(style);
