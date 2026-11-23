// Centralized env config with normalization.
// VITE_BASE_URL must point at the Cloudflare-proxied API domain in production.
// Never hardcode the origin host here: it would leak the deployment target.
const envBase = (import.meta.env.VITE_BASE_URL || "").trim();
const rawBase = envBase || (import.meta.env.DEV ? "http://localhost:8000" : "");

export const BASE_URL = rawBase.replace(/\/+$/, "");

export const SITENAME = import.meta.env.VITE_SITENAME || 'CloudXMovies';
export const TG_URL = import.meta.env.VITE_TG_URL || '';
export const TG_USERNAME = import.meta.env.VITE_TG_USERNAME || '';
export const BOT_USERNAME = import.meta.env.VITE_BOT_USERNAME || '';
// Legacy Turnstile/Firebase worker settings were removed with the old sign-in flow.

// ── Admin console backend URL ──────────────────────────────────────────────
// The public site and the admin console are built from the same source, and
// VITE_BASE_URL is baked in at build time. The dev env file is dev-only, so a
// production console build often has no backend URL at all: every /admin/api
// request then hits the console's own origin, gets the SPA index.html back, and
// every page looks "empty" with no useful error. A runtime override (set once
// on the login screen) fixes that without hardcoding a deployment target.
const ADMIN_BASE_KEY = "cx_admin_api_base";

function normalizeBase(value) {
  let base = String(value || "").trim().replace(/\/+$/, "");
  if (!base) return "";
  if (!/^https?:\/\//i.test(base)) base = `https://${base}`;
  try {
    const url = new URL(base);
    if (!["http:", "https:"].includes(url.protocol)) return "";
    // The console is hosted on Vercel while the API lives on a separate origin.
    // Allowing the SPA origin here makes /admin/api/* return index.html and
    // every admin page silently render empty.
    if (typeof window !== "undefined" && !import.meta.env.DEV &&
        url.origin === window.location.origin) return "";
    return url.origin + url.pathname.replace(/\/+$/, "");
  } catch {
    return "";
  }
}

export function getAdminBase() {
  const saved = getSavedAdminBase();
  if (saved) return saved;
  if (envBase) return normalizeBase(envBase);
  if (import.meta.env.DEV) return "http://localhost:8000";
  // Never fall back to the SPA origin (see normalizeBase). A missing base is
  // surfaced as an explicit error by the admin API client instead.
  return "";
}

export function setAdminBase(value) {
  const base = normalizeBase(value);
  try {
    if (base) localStorage.setItem(ADMIN_BASE_KEY, base);
    else localStorage.removeItem(ADMIN_BASE_KEY);
  } catch {
    /* storage can be blocked in private mode */
  }
  return base;
}

export function getSavedAdminBase() {
  try {
    return normalizeBase(localStorage.getItem(ADMIN_BASE_KEY) || "");
  } catch {
    return "";
  }
}

export function clearAdminBase() {
  try {
    localStorage.removeItem(ADMIN_BASE_KEY);
  } catch {
    /* storage can be blocked in private mode */
  }
}
