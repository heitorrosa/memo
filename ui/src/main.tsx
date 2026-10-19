import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { setToken, storedToken, tokenFromUrl } from "./lib/api";
import "./styles.css";

setToken(tokenFromUrl() || storedToken());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
