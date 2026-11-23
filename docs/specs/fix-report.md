# CloudXMovies — Fix Report

Companion to `frontend-ui-audit.md` (the findings) — this records what was actually changed.
Verified end-state: `npx vite build` succeeds, `npx eslint src` **0 errors**, admin API surface cross-checked against the backend.

---

## Verification summary

| Check | Before | After |
|---|---|---|
| Lint errors | 0 | **0** |
| Lint warnings | 148 | **108** |
| Production build | (worked, badly split) | **succeeds, 12 s** |
| Total bundle | 2,109 KB | **1,524 KB** (−28%) |
| `vendor-react` chunk | **0 KB** (broken split) | **137.8 KB** |
| Entry chunk | 969 KB | **80.5 KB** |
| 404 page chunk | 249 KB | **2.8 KB** |
| Admin API calls resolving | — | **66/66 = 100%** |
| Backend Python syntax | — | **OK** (4 files) |
| Body text below WCAG AA | 202 | **0** (decorative icons only) |

---

## 1. P0 — crash and data-loss fixes

**No error boundary (whole app white-screened).** Any render throw killed the page.
→ Added `components/ErrorBoundary.jsx` (class component, `role="alert"`, development-only stack details). Mounted twice: root (inside `BrowserRouter`, so the app can recover without a reload) and per-route (`compact`) so one bad page does not blank the chrome.

**`rating.toFixed(1)` on a string rating.** Ratings arrive as `"8.4"` in some documents; calling `.toFixed` threw `TypeError` inside the search-results render and took the app down.
→ Added `formatRating()` in `Nav.jsx`, used at both render sites. Non-numeric values render nothing instead of throwing.

**Inverted quality filter (silently hid most seasons).** `MoviesAndSeriesDetailsSections.jsx` *excluded* 480p/720p/1080p for series, so a season whose files were all 1080p produced an empty quality list; `QualityTab` returns `null` on empty, so the entire download / Telegram / subtitle block disappeared.
→ Filter now **includes** every quality. Also fixed the movie path: files live on the media document, not on episodes, so both `qualities` and `telegramByQuality` now fall back to `movieData.telegram`.

**Dead `/watch/...` route.** `QualityTab`'s most prominent button ("Watch") linked to `/watch/movie/...` and `/watch/series/...` — routes the router never defined, so it always hit the 404 page.
→ Becomes a `<button>` that scrolls to the real inline player (added `id="cx-player"` / `data-cx-player`), falling back to the direct stream when no player is mounted.

**Mobile search overlay could permanently freeze scrolling.** `body{overflow:hidden}` was released only in the effect cleanup, but the overlay is also `hidden` above `md` — so opening it at phone width then widening the viewport left the lock applied and the page unscrollable.
→ Lock now releases whenever the overlay is not actually visible (`offsetParent === null`), re-checked on `resize`.

**Two watch-history keys.** `utils/watchHistory.js` writes `fc_watch_history`; `usePersonalizedRecommendations` read `watchHistory`, which only the unmounted `WatchHistoryProvider` ever wrote — so personalisation always saw an empty history and silently degraded to the plain backend list.
→ Hook now reads the real store; the dead context was deleted.

---

## 2. P1 — error states, where "empty" hid "broken"

**`safe(promise) = .catch(() => [])`** in `sectionsConfig.js` converted every backend failure into "no results". `SmartSection` then unmounted the row, so a backend outage rendered a **blank homepage with no message and no way to recover**.
→ `safe()` no longer swallows. `SmartSection` rewritten to distinguish three outcomes (empty / failed / loaded), with a `SectionError` row carrying a **Retry** button. Reserved-height sentinel prevents the layout jump when the fetch triggers.

**Hero returned `null` on any error.** `Home.jsx` only fell back to latest-releases on a **404**, but the endpoint raises **400**.
→ Falls back for *any* failure, accepts `tv_shows`, and renders a `HeroFallback` panel (reserved height, "Try again" + "Browse movies") instead of deleting the top of the page.

**Eight browse pages each swallowed their error** with `.catch(() => setLoading(false))` — a 500 was indistinguishable from an empty catalogue.
→ New `hooks/useMediaList.js` (abortable, race-guarded, real `error` state + `reload`) and `components/MediaListPage.jsx`. `Movies`, `Series`, `Anime`, `KDrama`, `TopRatedMovies`, `TopRatedSeries`, `MostViewed` now share it and show an `EmptyState` with **Retry**. Each page shrank to ~15 lines.

**Player had no failure state.** A dead `/dl/` link left a black box with dead controls.
→ `InlinePlayer` listens for `error`/`stalled`, shows a `role="alert"` panel with **Try again** (reloads the source) and **Reload page**, hides the toolbar while failed.

**Search failure looked like "No results".** Also `isLoading` stuck `true` forever after clearing the query mid-flight (endless "Searching…").
→ Added `searchError`, check `res.ok`, clear the spinner when the query empties; both desktop dropdown and mobile overlay render a distinct "Search is unavailable" state.

---

## 3. Responsive — desktop header at the wrong breakpoint

The full desktop header (logo + 5 icon+label links + My List + search field) needs **~990 px**, but it switched on at `md` = **700 px**, so from 700–990 px the nav compressed into the search input.
→ Added a dedicated `navlg: 990px` breakpoint and retargeted the desktop nav, mobile search icon, desktop search, mobile overlay, bottom dock, **and the `<main>` padding** onto it. A private breakpoint was used rather than changing `md`, so none of the app's other `md:` utilities reflowed. Confirmed emitted in the built CSS (media widths now include `990px`).

Also: the mobile search overlay was not a dialog — added `role="dialog"`, `aria-modal`, `aria-label`, Escape-to-close, and a Tab focus trap.

---

## 4. Design tokens

- **`fontSize` was flat**, so every `text-*` fell back to `normal` leading — the reason `leading-snug`/`leading-tight` is hand-written throughout. Sizes are unchanged (the app is tuned to them); each step now carries a paired `lineHeight`.
- **Promoted `projector-bright` (#F0CA98)** which existed only as an arbitrary value 21 times, and added `bone-muted` / `bone-subtle` as the accessible text ramp (measured 6.1:1 and 4.8:1 on `#101`-class ink).
- Comments added explaining each choice.

---

## 4b. Contrast — brought body text to WCAG AA

Secondary body text used five opacity tiers below the AA threshold. Measured against the site ink (`#08090B`), `text-bone` reads:

| Utility | Contrast | Verdict |
|---|---|---|
| `/20` | ~1.9:1 | fail |
| `/25` | ~2.2:1 | fail |
| `/30` | ~2.5:1 | fail |
| `/35` | 2.78:1 | fail |
| `/40` | 3.36:1 | fail |
| `/45` | 4.01:1 | fail |
| `/50` | 4.72:1 | **pass** |

**202 instances across 60 files** were below AA — roughly a third of the app's secondary text, and worst in the admin console where dense tables of `/35` labels were genuinely hard to read.

Rather than flattening five tiers into one and losing the hierarchy, the tiers were **remapped into the compliant band**: `/20`,`/25` → `/50`; `/30`,`/35` → `/55`; `/40`,`/44` → `/60`; and the residual `/45` → `/50`. Hierarchy is preserved, every step now passes.

Only `text-bone/*` was touched. Borders, fills and icon colours were deliberately left alone — their contrast floor is 3:1 (non-text), not 4.5:1, and they already met it. The single remaining `text-bone/10` is a decorative 64 px icon with no informational role.

---

## 5. CSS cleanup (`index.css`)

- **Removed `!important` from the input rules.** Four near-identical blocks stamped `background-color: … !important` on every field, beating the inline `backgroundColor` on the mobile search field — which rendered **black text on a black box**. Now a default the component can override; only the WebKit `-webkit-text-fill-color` fix stays important (no inline equivalent). `input[type=search]` no longer paints an opaque box.
- **Deleted the `* { margin:0; padding:0 }` reset** that duplicated Preflight (kept `box-sizing`).
- **Removed `html { scroll-behavior: smooth }`** — it animated *every* route change; smoothed scroll now only applies under `prefers-reduced-motion: no-preference` for in-page anchors.
- **Deleted the `#player { rotate(90deg) scale(1.75) }` portrait hack.** Nothing renders `id="player"`; it was an inert landmine that would have rendered any future element sideways on every phone.
- **Removed duplicate `.skeleton`, `.loader-episode`, `.no-scrollbar`** definitions whose later copies silently shadowed richer earlier ones.

---

## 6. Bundle — `vendor-react` was 0 KB

Object-form `manualChunks` resolved the packages but left React inlined in the entry: the emitted `vendor-react` was **0 bytes** and absent from modulepreload, so every deploy invalidated React + all app code under one hash.
→ Converted to the **resolver-function** form matching on normalised module ids. React is now a real 137.8 KB chunk, entry dropped 969 → 80.5 KB.

**Deleted the Lottie dependency from the 404 route.** It pulled `lottie-react` (~317 KB) into a route nobody wants to see, making `NotFound` the heaviest chunk in the app at 249 KB. Replaced with an inline SVG film reel: **2.8 KB**, and `vendor-lottie` is gone entirely.

---

## 7. Dead code removed (24 modules)

Verified unreferenced (no live import anywhere) before deletion:

`components/` — ContinueWatching, GenreSection, MobileMovieDetails, BottomNav, PlayerModal, PageTransition, PrivateRoute, Logout, AudioTrackSelector, SubtitleSelector, TrailerModal
`pages/` — MobileDownloads, MobileProfile, MobileSearch, OfflineFallback, Login, Token
`hooks/` — useClickOutside, useDebounce, useTouchGestures
`context/` — WatchHistoryContext, AuthContext
plus `firebase.jsx`, `components/signInWIthGoogle.jsx`

The Firebase auth chain was wholly unreachable — no route rendered `Login`, and the admin console uses its own `X-Admin-Session` token auth. Removing it also removed an entire unused SDK from the dependency graph.

---

## 8. Admin console

**Audit result: the API layer was correct.** All **66** `api.*` calls across 21 modules resolve to real backend routes (61 `/admin/api/*` routes scanned). Response shapes match the backend returns for `/media`, `/media/bulk`, `/views-analytics`, `/index-scans`, `/jobs`, `/audit`, `/hero`, `/config`, `/failed-files`. Queue payloads (`{confirm:"CLEAR"}`), job `kind` strings (`audio_lang_backfill` / `metadata_remediation` / `orphan_scan`), hero `{picks}` shape, bulk action enum, and DELETE query-vs-body all verified against the handlers. No stub pages, no empty `onClick`, no silent catches.

Two genuine bugs found and fixed:

**`SettingsPage` coercion.** The line was `row.type === "int" ? Number(raw) : Number(raw)` — both branches identical. Clearing a field produced `Number("") === 0`, which is a valid in-range integer for most knobs, so **emptying an input and pressing Save silently wrote 0**. Non-numeric text produced `NaN`, which serialises to `null` and came back as a confusing server error.
→ Explicit per-type coercion with pre-flight validation (empty / non-numeric / non-integer / out-of-range all rejected with a specific message before the request).

**Season 0 treated as "no season".** `epParams` and five other guards used truthiness (`q._season ? … : {}`). Season **0** is legitimate — TMDB uses it for Specials — so file, subtitle and episode edits on Specials dropped the season/episode context and targeted the wrong document.
→ Introduced `hasSeason()` using `!= null` and routed every guard through it.

---

## 9. SEO / PWA

- **`og:image` was an SVG** — Facebook, WhatsApp, Slack and iMessage reject SVG, so **every shared link produced a blank preview card**. Generated a real **1200×630 PNG** (`public/og-default.png`) with the brand palette; it is now the default. Also added `og:image:alt`, `twitter:image:alt`, `og:locale`, and absolute-URL resolution for relative images.
- **`noIndex` support.** Search results (every distinct query was an indexable near-duplicate), cast pages and My List were all `index, follow` — burning crawl budget on thin pages. They now emit `noindex, follow` and are dropped from the sitemap.
- **JSON-LD** added to movie (`Movie`) and TV (`TVSeries`) detail pages — name, description, rating with `bestRating: 10`, year, image, genre, season/episode counts — to earn proper rich results.
- **Service worker cached `/api/` for 24 h.** Playback URLs (`/dl/...`) expire server-side, so viewers got dead links that only recovered by clearing site data. Reduced to **5 minutes**, with `/api/stream/` and `/api/dl/` excluded outright and `method: GET` pinned.

---

## 10. Other correctness fixes

- **Drag-scroll opened the wrong title.** The guard tested `isDragging` on `click`, but `click` fires *after* `mouseup` — by which time `isDragging` is already `false`. So releasing a drag navigated to whatever card the cursor was over. Now tracks `dragMoved` and suppresses via `onClickCapture`.
- **`key={index}`** in `HomeSections` / `MoviesAndSeriesSections` made React reuse the wrong card across filter/page changes (poster and links briefly belonged to a different title). Now keyed on identity.
- **`TopTenToday` posters letterboxed** — `LazyLoadImage` had no `wrapperClassName`, so the wrapper collapsed to intrinsic size instead of filling the 2:3 frame. Matched to `MovieCard`'s working pattern.
- **`CastSection` deleted the focus ring** (`focus-visible:outline-none` with no replacement). Restored.
- **Two homepage shelves rendered identical films** — "Popular Movies" and "Top Rated Movies" both fetched `rating:desc`, as did the TV pair. "Popular" now means most-watched, so both pairs read distinctly.
- **Anime/genre pagination dropped ~half of every page.** Each collection was fetched with the full `limit`, merged, then truncated back to `limit` — so ranks below the top-N of each window were unreachable, and every page re-ranked a fresh disjoint window, leaving permanent gaps. Added `_merged_page()` which reads `page * limit` per side, merges, sorts, then slices the true window. (Backend, `database.py`.)
- **`TvDetails` double-fetched** `/api/id/:id` on every season change, refetching the whole show (including the cast fallback) to read one season already present in the loaded document. Now derived locally.
- **Four `ToastContainer` mounts** (Home, Login, MovieDetails, TvDetails) meant toasts fired on one route never appeared on another. Hoisted to a single mount in the shell.
- **`Movies.jsx` re-filtered genre client-side using only `genresArr[0]`**, discarding valid results the backend had already filtered correctly. Removed — the backend does this properly.
