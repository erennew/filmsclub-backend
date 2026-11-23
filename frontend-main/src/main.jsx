import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HelmetProvider } from "react-helmet-async";

import App from "./App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { startContentRefresh } from "./utils/appLifecycle.js";
import "./index.css";

/*
 * #root ships with a static inline-styled boot shell (see index.html) so a
 * failed bundle shows a branded recovery screen instead of a blank white page.
 * createRoot does not clear existing children — it appends alongside them and
 * logs a warning — so the shell is removed here, immediately before mount.
 */
const container = document.getElementById("root");
const bootShell = document.getElementById("boot-shell");
if (bootShell) bootShell.remove();

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <HelmetProvider>
        <App />
      </HelmetProvider>
    </ErrorBoundary>
  </StrictMode>
);

startContentRefresh();

// Workbox service worker is generated at build time. Registration is manual so
// the app can surface a "new version" toast instead of reloading mid-session.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  import("virtual:pwa-register")
    .then(({ registerSW }) => {
      const updateSW = registerSW({
        immediate: true,
        onNeedRefresh() {
          window.__pwaUpdateReady = true;
          window.dispatchEvent(new Event("pwa:update-available"));
        },
        onOfflineReady() {
          window.dispatchEvent(new Event("pwa:offline-ready"));
        },
        onRegisteredSW(_swUrl, registration) {
          if (!registration) return;
          const checkForUpdate = () => registration.update().catch(() => {});
          window.setInterval(checkForUpdate, 60 * 60 * 1000);
          document.addEventListener("visibilitychange", () => {
            if (document.visibilityState === "visible") checkForUpdate();
          });
        },
        onRegisterError(error) {
          console.warn("Service worker registration failed:", error);
        },
      });

      window.addEventListener("pwa:apply-update", () => updateSW(true));
    })
    .catch((error) => console.warn("PWA registration module failed:", error));
}
