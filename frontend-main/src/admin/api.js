/* Admin API client: token session storage + fetch wrapper for the FilmsClub backend. */
import { getAdminBase, setAdminBase, getSavedAdminBase, clearAdminBase } from "../config";

export { setAdminBase, getSavedAdminBase, clearAdminBase, getAdminBase };

const TOKEN_KEY = "cx_admin_token";

export const getToken = () => sessionStorage.getItem(TOKEN_KEY) || "";
export const setToken = (token) => sessionStorage.setItem(TOKEN_KEY, token);
export const clearToken = () => sessionStorage.removeItem(TOKEN_KEY);

const listeners = new Set();

/* Fired on 401 (expired/invalid session) so the shell can bounce back to login. */
export function onAuthEvent(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function fireAuthEvent(type) {
  listeners.forEach((fn) => fn(type));
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/* True when a failed request means the connected backend is an older build or
   the console is pointed at the wrong origin. Pages use this to show one shared,
   actionable notice instead of repeating FastAPI's bare "Not Found" six times.
   Accepts either the ApiError itself or an already-extracted message string,
   because most pages store `e.message` in state. */
export function isStaleBackendError(error) {
  const status = error && typeof error === "object" ? Number(error.status) || 0 : 0;
  const message =
    typeof error === "string" ? error : (error && error.message) || "";
  if (status === 404) return true;
  return /older build|missing on the connected backend|points at the frontend|predates the compatibility endpoint/i.test(
    message
  );
}

async function request(path, { method = "GET", params, body } = {}) {
  const base = getAdminBase();
  if (!base) {
    throw new ApiError(
      "No backend URL configured. Open Backend connection and enter the backend API URL.",
      0
    );
  }
  let url = `${base}${path}`;
  if (params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
    );
    const s = qs.toString();
    if (s) url += `?${s}`;
  }
  const headers = {};
  const token = getToken();
  if (token) headers["X-Admin-Session"] = token;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Backend unreachable — check your connection or the API URL.", 0);
  }

  let data = null;
  let rawText = "";
  try {
    rawText = await res.text();
    data = JSON.parse(rawText);
  } catch {
    data = { detail: rawText };
  }

  // A console hosted on a different origin returns its own index.html for
  // /admin/api/... (SPA rewrite). Say so plainly instead of showing an empty
  // page with an unreadable HTML string as the error.
  const looksLikeHtml = /^\s*<!doctype|<html|<\/html>/i.test(rawText || "");
  if (looksLikeHtml) {
    throw new ApiError(
      "The API URL points at the frontend, not the backend. Open the connection " +
        "settings (gear icon) and set the backend URL.",
      0
    );
  }

  if (!res.ok) {
    if (res.status === 401 && token) fireAuthEvent("expired");
    if (res.status === 404 && path.startsWith("/admin/api/")) {
      // Every path below exists in the current backend. A 404 therefore means
      // the connected process is an older build (or the URL is wrong), not a
      // broken feature — say so instead of showing FastAPI's bare "Not Found".
      throw new ApiError(
        `${path} is missing on the connected backend (404). It is running an ` +
          "older build — deploy/restart the current backend, or fix the API URL " +
          "in connection settings.",
        res.status,
      );
    }
    throw new ApiError(data?.detail || `Request failed (${res.status})`, res.status);
  }
  return data;
}

export const api = {
  get: (path, params) => request(path, { params }),
  post: (path, body, params) => request(path, { method: "POST", body, params }),
  patch: (path, body, params) => request(path, { method: "PATCH", body, params }),
  delete: (path, params) => request(path, { method: "DELETE", params }),
};

/* Build marker the console was compiled against. The backend reports its own
   marker plus its live route table so a stale deploy is detected once, after
   login, instead of surfacing as six separate "Not Found" pages. */
export const EXPECTED_ADMIN_BUILD = "2026-11-07";

export const REQUIRED_ADMIN_ROUTES = [
  "/admin/api/capabilities",
  "/admin/api/files/lookup",
  "/admin/api/files/reassign",
  "/admin/api/session",
  "/admin/api/media",
  "/admin/api/hero",
  "/admin/api/index-scans",
  "/admin/api/orphans",
  "/admin/api/audio-curation",
  "/admin/api/config",
  "/admin/api/audit",
  "/admin/api/queue",
  "/admin/api/jobs",
  "/admin/api/health",
  "/admin/api/cache",
  "/admin/api/channels",
  "/admin/api/failed-files",
  "/admin/api/library-health",
  "/admin/api/analytics",
  "/admin/api/views-analytics",
  "/admin/api/storage",
];

export async function checkBackendCompatibility() {
  const info = await api.get("/admin/api/capabilities");
  const routes = new Set(info.routes || []);
  const missing = REQUIRED_ADMIN_ROUTES.filter((path) => !routes.has(path));
  return {
    version: info.version || "unknown",
    build: info.build || "unknown",
    missing,
    outdated: missing.length > 0 || info.build !== EXPECTED_ADMIN_BUILD,
  };
}

export async function login(username, password) {
  const data = await request("/admin/api/login-token", {
    method: "POST",
    body: { username, password },
  });
  setToken(data.token);
  fireAuthEvent("signed-in");
  return data;
}

export async function logout() {
  clearToken();
  try {
    await api.post("/admin/api/logout");
  } catch {
    /* best effort — the header token is cleared client-side anyway */
  }
  fireAuthEvent("signed-out");
}
