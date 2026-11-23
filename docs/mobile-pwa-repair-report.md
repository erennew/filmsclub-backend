# Mobile PWA Repair — Delivery Report

Date: 2026-11-09
Scope: white-screen root cause, mobile responsiveness, PWA correctness, error audit, performance, mobile UI polish.

---

## 1. Root cause of the white screen

**The reported symptom was not reproducible in the current code**, so I measured instead of guessing.

I ran a full sweep of every route across 10 viewport widths (320 → 1440) on both a local build and the
live deployment, using a DevTools Protocol harness that measures `documentElement.scrollWidth` and
`getBoundingClientRect()` for every element.

Result:

| Check | Finding |
|---|---|
| Horizontal overflow (`scrollWidth - innerWidth`) | **0 px on all 150 route × width combinations** |
| Body width vs viewport | **100 % at 320 / 360 / 375 / 390 / 412 / 430 / 480px** |
| Near-white pixels in full-page screenshots | **0.3–0.9 %** (text glyphs only); **0 columns** above 245 luminance |
| `100vw` / `w-screen` misuse in mobile layout | none (only `min-w-[640px+]` on admin tables, already inside `overflow-x-auto`) |
| Desktop chrome leaking into mobile | none — `navlg` (990px) switch is consistent |

So the "content fills 75 % with a white strip on the right" is **not a layout bug in this codebase**.
The screenshot did not reach me as a file, so I could not read the exact pixels the user saw.

### What the white screen actually is

I reproduced the only mechanism in this app that produces a genuinely white screen, and confirmed it
with pixel measurement:

| Failure simulated | Result before fix |
|---|---|
| Entry JS bundle blocked (stale `index.html` → deleted hash, blocked/aborted chunk, SW returning a bad asset) | `#root` empty, **100 % white screen, mean luminance 255** |
| CSS bundle blocked | still renders (2.6 % light) — not the cause |

**Root cause: `<html>` had no background.** `body` was set to `--ink`, and a body background normally
propagates to the document canvas — so in the healthy case nothing looked wrong. But when JS fails to
mount, `#root` stays empty, the body has no content height to paint behind, and the canvas falls back to
the browser default, which is **white**. That is the white screen.

This also explains a *partial* white strip (content narrower than the viewport) if the app half-mounted:
the uncovered area reveals the same unbacked canvas.

### Fixes (both layers)

1. **`html { background-color: var(--ink); color-scheme: dark }`** — the canvas is ink even when
   nothing paints. Verified **0 % white with JS loaded** (no visual change in the healthy case) and
   **100 % → 0 % white with the entry bundle blocked**.
2. **Static boot shell in `index.html`** — inline-styled, dependency-free recovery UI inside `#root`
   before React mounts, with a watchdog (script `error` capture + 4 s timeout) that reveals a
   "didn't finish loading → Reload" button. Survives CSS *and* JS failure because it needs neither.
   `main.jsx` removes the shell immediately before `createRoot` to avoid React's non-empty-root warning.

Verified: blocking the entry hash now yields a branded ink screen with recovery UI (88 chars of text)
instead of a blank white page.

---

## 2. Confirmed issues found and fixed

| # | Issue | Evidence | Fix |
|---|---|---|---|
| 1 | `<html>` had no background → blank white canvas when JS fails | 100 % white, mean lum 255, with entry blocked | ink background on `html` |
| 2 | No recovery UI when the bundle fails | `#root` child count 0 | static boot shell + watchdog |
| 3 | Images requested at `image.tmdb.org/t/p/original` | Lighthouse: 1575 KB, 1181 KB, 1101 KB hero/card images; one took **20.4 s** | `utils/imageUrl.js` rewrites the size segment |
| 4 | Poster colour extraction downloaded the full poster to sample **one pixel** | `new Image(); img.src = src` on details pages | uses `w92` (~3 KB) |
| 5 | NextUI (237 KB) preloaded on **every** page, used by one lazy route | 568 KB unconditional `modulepreload`; `@nextui-org/system` imported in `main.jsx` | provider moved into the details route; `manualChunks` no longer pins it to a shared chunk |
| 6 | Google Fonts stylesheet render-blocking | cross-origin `<link rel="stylesheet">` in `<head>` | `media="print"` + `onload` swap, `preload`, `<noscript>` fallback |
| 7 | No fetch timeout — a stalled API hung the promise forever | `fetch()` with no `AbortController` | 8 s `AbortController`, timer cleared in `finally` |
| 8 | Splash gated on the hero fetch, holding the whole app | `SplashScreen ready={!heroLoading}` | splash closes on its own timing; hero shows its own skeleton |
| 9 | Hashed assets served `max-age=0, must-revalidate` | all 40 JS + 4 CSS revalidated on every visit | `vercel.json` sets `immutable` for `/assets/*` |
| 10 | Stale `90px` dock fallbacks after the dock shrank to 73px | 3 files | corrected to `73px` |
| 11 | No `vercel.json` at all | none present | added rewrites + cache headers |

### Non-issues I verified and left alone
- **No horizontal overflow** anywhere (150 combinations).
- **No duplicate API calls** (measured per route).
- **Precache is correct** — 74 entries / 1.9 MB includes all JS + CSS (my first parse missed entries
  because the manifest writes `{url, revision}` in two orders).
- **Offline UI already exists** — `PWAInstallPrompt` renders an "Offline · cached view" banner. I wrote
  a new `OfflineBanner` component, discovered the duplicate, and **deleted mine**. No redundant component shipped.
- **NextUI is genuinely used** (`Button`, `Popover`, `Select` in `Buttons.jsx` / `TelegramButtons.jsx`), so
  it was not removed — only deferred.

---

## 3. Files modified

| File | Change |
|---|---|
| `frontend-main/src/index.css` | ink background + `color-scheme` on `html`; `73px` dock fallback |
| `frontend-main/index.html` | non-blocking font link + preload + `<noscript>`; static boot shell + watchdog |
| `frontend-main/src/main.jsx` | remove `NextUIProvider` from root; strip boot shell before mount |
| `frontend-main/src/utils/imageUrl.js` | **new** — TMDB size rewriting (`sizedImage`, `posterUrl`, `backdropUrl`, srcset, `isOversized`) |
| `frontend-main/src/utils/apiCache.js` | 8 s `AbortController` timeout |
| `frontend-main/src/components/MoviesAndSeriesDetailsSections.jsx` | provider wrapper; backdrops 640 px |
| `frontend-main/src/pages/MovieDetails.jsx` | palette uses `w92` |
| `frontend-main/src/pages/TvDetails.jsx` | palette uses `w92` |
| `frontend-main/src/pages/Home.jsx` | splash ungated from hero fetch |
| `frontend-main/src/components/HomeHero.jsx` | `backdropUrl(900)`, `visibleByDefault` |
| `frontend-main/src/components/MovieCard.jsx` | `posterUrl()` |
| `frontend-main/src/components/TopTenToday.jsx` | `posterUrl()` |
| `frontend-main/src/components/Nav.jsx` | search thumbs `posterUrl(44)` |
| `frontend-main/src/components/MobilePlayBar.jsx` | `73px` fallback |
| `frontend-main/src/components/PWAInstallPrompt.jsx` | `73px` fallback |
| `frontend-main/vite.config.js` | NextUI/react-aria no longer pinned to a shared chunk |
| `frontend-main/vercel.json` | **new** — SPA rewrites, asset/CSS/HTML caching, manifest type |

---

## 4. Performance improvements

Measured image cost (live TMDB, `Content-Length`):

| Asset | Before | After | Saving |
|---|---|---|---|
| Hero backdrop | 1368 KB (`original`) | 128 KB (`w1280`) | **10.7×** |
| Grid poster | 951 KB (`original`) | 13 KB (`w342`) | **73×** |
| Nav search thumb | full poster | 4 KB (`w92`) | — |

Bundle:

| Metric | Before | After |
|---|---|---|
| Unconditional first-load JS | 568 KB raw / ~185 KB gzip | **331 KB raw / 103 KB gzip** (5 chunks) |
| NextUI on non-details routes | 237 KB, always | 0 — loads only on details |
| `original`-size image requests | 3+ per page view | **0** (verified across all routes) |

### Lighthouse

| Metric | Baseline (live Vercel) | Optimised (local) |
|---|---|---|
| Performance | **39** | **66** |
| LCP | 24.8 s | **6.3 s** |
| TBT | 1,400 ms | **320 ms** |
| Speed Index | 15.5 s | **3.2 s** |
| TTI | 24.8 s | **8.4 s** |
| CLS | 0 | 0 |

**Caveat, stated plainly:** the baseline was measured against Vercel (brotli + CDN + real backend); the
"after" is a local static server with unstripped responses and a mock API. The comparison is directionally
valid for image weight and JS payload (both measured directly), but the absolute score is not
apples-to-apples. A true after-score requires deploying — which I have not done.

Targets not yet met: Performance 90+, LCP < 2.5 s. Remaining cost is 273 KB of unused CSS (NextUI's
generated theme, which is **required at runtime** — see §6) and the tail of unused JS.

---

## 5. Mobile viewport testing

150 route × width combinations, **0 px horizontal overflow**:

Widths: 320, 360, 375, 390, 412, 430, 480, 768, 1024, 1440
Routes: `/`, `/Movies`, `/Series`, `/anime`, `/k-drama`, `/top-rated-movies`, `/top-rated-series`,
`/most-viewed`, `/my-list`, `/mov/:id`, `/ser/:id`, `/search/:q`, `/genre/:type/:name`, `/Cast/:id/:name`,
`/similarMov/:id`, plus a 404 route.

Also verified: real-device DPR emulation (Pixel 7 @2.625, Galaxy @3, iPhone 14 @3, SE1 @2) all report
`innerWidth === scrollWidth === body width` at 100 %. Desktop keeps its chrome and hides the mobile dock.

---

## 6. Remaining issues

1. **Unused CSS — 273 KB.** This is NextUI's generated theme. I tried removing the
   `./node_modules/@nextui-org/theme/dist/**/*` content glob: CSS dropped 299 KB → 105 KB, but the
   NextUI `Select`'s runtime-computed classes then had no styles. **Reverted** — the glob is load-bearing.
   A safe reduction needs replacing `Select`/`Popover` with plain markup, which is a design change.
2. **Detail pages still use `axios` directly** (`/api/id/:id`), so the new 8 s timeout covers `getJSON`
   callers only. They already have error states + `RetryButton`, so behaviour is acceptable, but the
   timeout is not unified.
3. **Lighthouse accessibility / best-practices / SEO not measured** — the sandbox's Lighthouse run fails
   on `Storage.getUsageAndQuota`. Only performance was captured (via `--disable-storage-reset`).
4. **`/api/trending/week` returns 770 KB** (20 items × ~2.8 KB, including `sources` arrays full of playback
   URLs the card never renders). Fixing this needs a backend `fields=` projection — out of scope without
   touching the backend.
5. **Dead code**: `MobileBottomNav.jsx` and `PullToRefresh.jsx` still have 0 imports.
6. **Pre-existing mojibake** in `frontend-main/src/config.js` and comment banners (`â”€â”€`, `Â·`) — not from
   this work, left untouched.

---

## 7. Vercel deployment instructions

`vercel.json` is now present at `frontend-main/`. It assumes the project root is `frontend-main`.

```
Root Directory : frontend-main
Build Command  : vite build
Output Directory: dist
Framework Preset: Vite
```

Required env vars (baked at build time, see `.env.example`):
`VITE_BASE_URL`, `VITE_SITENAME`, `VITE_TG_URL`, `VITE_TG_USERNAME`, `VITE_BOT_USERNAME`,
`VITE_SITEKEY`, `VITE_WORKERURL`.

Deploy from `frontend-main/`:

```
vercel --prod
```

**Important:** `frontend-main/.env.local` contains a `VERCEL_OIDC_TOKEN` created by the CLI. It is
gitignored, but delete it if you are not deploying from this checkout.

After deploying, hard-reload once. The service worker is `skipWaiting: false`, so existing installs get
the "new version available" toast and swap on reload — that is deliberate (prevents mid-session reloads).

---

## 8. Manual testing still required

Everything below needs a real device or a deployed environment — I could not verify it here:

1. **Actual white-screen report** — I could not reproduce it and could not see the screenshot. If it
   recurs, capture the browser console + whether the URL is the installed PWA or a browser tab.
2. **Installed PWA standalone mode** — CDP cannot emulate `display-mode: standalone`
   (`Emulation.setEmulatedMedia` has no effect on it), so safe-area/status-bar behaviour is unverified.
3. **Post-deploy Cache-Control** — confirm `/assets/*` returns `immutable` and `/sw.js` returns
   `max-age=0` (`curl -I`). My `vercel.json` headers are unverified against a live deploy.
4. **Update flow** — deploy twice and confirm the toast appears and the reload picks up new hashes.
5. **Android hardware** — verify 320–430 px layout, dock safe area, and the offline banner on a physical device.
6. **Backend-dependent pages** — no backend runs locally; API shape was mocked for testing.

---

## Verification summary

Build ✅ · eslint **0 errors / 107 warnings** (unchanged baseline) · 150 route×width overflow checks **0**
· `original` images **0** · uncaught exceptions **none** (excluding the known local
`/_vercel/insights` 404, which is Vercel Analytics requesting a production-only endpoint) · NextUI
popover and download menu still render · details route still lazy-loads NextUI correctly.
