import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { setToken, storedToken, tokenFromUrl } from "./lib/api";
import "./styles.css";

// The client token must exist before the first request: a token handed over in
// the URL wins over (and replaces) whatever was stored.
setToken(tokenFromUrl() || storedToken());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
