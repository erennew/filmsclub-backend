import { clearJSONCache } from "./apiCache.js";

const REFRESH_COOLDOWN_MS = 1000;

export function startContentRefresh({
  document: appDocument = globalThis.document,
  window: appWindow = globalThis.window,
} = {}) {
  let lastRefreshAt = 0;

  const refresh = () => {
    const now = Date.now();
    if (now - lastRefreshAt < REFRESH_COOLDOWN_MS) return;

    lastRefreshAt = now;
    clearJSONCache();
    appWindow.dispatchEvent(new CustomEvent("cloudx:content-refresh"));
  };

  const onVisibilityChange = () => {
    if (appDocument.visibilityState === "visible") refresh();
  };

  const onPageShow = (event) => {
    if (event.persisted) refresh();
  };

  appDocument.addEventListener("visibilitychange", onVisibilityChange);
  appWindow.addEventListener("pageshow", onPageShow);

  return () => {
    appDocument.removeEventListener("visibilitychange", onVisibilityChange);
    appWindow.removeEventListener("pageshow", onPageShow);
  };
}
