import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { installHooks, pullFromServer } from "./serverSync";

installHooks();

const root = createRoot(document.getElementById("root")!);

(async () => {
  try {
    await pullFromServer();
  } catch (err) {
    console.error("Не удалось загрузить данные с сервера:", err);
  }
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
})();
