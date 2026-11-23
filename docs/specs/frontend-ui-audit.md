# Frontend UI/UX Audit — Master Report

**Project:** CloudXMovies (film/streaming site) — `frontend-main/`
**Stack:** React 18 · Vite 5 · Tailwind 3.4 · framer-motion · NextUI · Plyr · Swiper · PWA (Workbox)
**Date:** 2026-11-08
**Method:** 5 parallel deep audits over disjoint file scopes + verified build/lint/config measurements.
**Source reports:** [audit-shell](audit-shell.md) · [audit-discovery](audit-discovery.md) · [audit-details](audit-details.md) · [audit-player](audit-player.md) · [audit-system](audit-system.md)

**Total: 173 findings — 9 P0 · 41 P1 · 90 P2 · 33 P3.** No source files were modified.

---

## 1. Executive summary

The visual design is genuinely good — a coherent dark "late-night screening room" identity, real editorial typography (Fraunces + Manrope + IBM Plex Mono), proper `Lens`-based poster cards, and a PWA setup whose deep-link handling is correct. The problems are **not aesthetic**. They are:

1. **The app has no failure states.** A backend error renders a *blank homepage*; nine browse pages report a 500 as "This shelf is still being stocked"; a dead stream renders a black box with no retry. This is the single most damaging theme in the audit.
2. **The design system was never enforced.** 299 arbitrary `text-[Npx]` declarations, 49 distinct `bone`-opacity steps, 18 border-radius tokens, 75 raw hex values, and a `fontSize` override that silently shrank `text-sm` to 0.8rem *and* stripped every default line-height. The codebase is fighting its own tokens.
3. **Two P0 build defects** make the first paint cost **2,109 KB raw / 494 KB gzip**: `vendor-react` is a 0 KB chunk (React inlined into the entry), and there is exactly **one** dynamic import in 107 modules.
4. **~2,300 lines of dead code** ship in the bundle — including an entire alternate "mobile v2" UI that references a second, undefined CSS-token system.

Fixing themes 1 and 3 is worth more than all 90 P2 findings combined.

---

## 2. Verified corrections (things we assumed and disproved)

Good engineering hygiene means stating what turned out *not* to be true. Three premises in the brief were wrong, and the auditors proved it rather than repeating them:

| Claim | Verdict | Evidence |
|---|---|---|
| Out-of-order `screens` keys break CSS cascade | **False** | Tailwind sorts breakpoints numerically at build time. A compile with the repo's own Tailwind 3.4.19 emits ascending order: `360, 400, 480, 500, 700, 750, 920, 1024, 1224, 1440, 1536, 1600`. Declaration order is a readability trap only. |
| There is mojibake (`â€"`) in `index.html` / the manifest | **False** | Byte-scan of all 137 source files + built output: zero double-encoded sequences. The em dash is valid UTF-8 `E2 80 94`. The `â€"` I saw was a PowerShell console codepage artifact. |
| A duplicated mobile+desktop detail tree double-renders | **False** | `MobileMovieDetails.jsx` is imported by **nothing**. No double DOM, no double fetch from that cause. The real double-fetch is `TvDetails` hitting `/api/id/:id` twice. |

Things also confirmed **already correct** (don't "fix" these): `navigateFallbackDenylist` (deep links like `/mov/123` work offline), `prefers-reduced-motion` CSS handling in `index.css:1053-1071`, `react-toastify` is genuinely used, `<div onClick>` count is **zero** (252 onClick handlers, all on real `<button>`/`<Link>`), and `MovieCard.jsx` is a model implementation.

---

## 3. P0 — Blockers (9)

| # | Finding | File | Why it blocks |
|---|---|---|---|
| **1** | **No error boundary anywhere.** A single render throw blanks the entire product with no nav, no reload affordance. | `App.jsx:34-40` | PWA users get a permanent white screen. Provable throw site: `Nav.jsx:55,106` call `result.rating?.toFixed(1)` — optional chaining guards `null`, not strings, and `rating` is stored untyped in Mongo. A `"7.5"` document crashes the app. |
| **2** | **`vendor-react` chunk is 0 KB** — React is inlined into the 969 KB entry. | `vite.config.js:180` | Every deploy invalidates React + ReactDOM + all app source in one hash. The manual-chunk strategy is entirely defeated; all 8 other vendor chunks *are* preloaded, `-react` is not. |
| **3** | **Zero route-level code splitting** — one dynamic import in 107 modules. | `App.jsx:1-19,62-80` | 2,109 KB raw / 494 KB gzip before first paint, including the admin console, Plyr and Swiper on the homepage. |
| **4** | **Backend failure renders a blank homepage.** `Home.jsx` falls back to `/api/movies` only on **404**, but the hero endpoint raises **400**. | `Home.jsx:59-70` → `HomeHero.jsx:28` | `heroMovies=[]` → `return null` *removes* the hero; every section's `safe()→[]` → `SmartSection:83` unmounts. Splash closes onto an empty page. |
| **5** | **Nine browse pages report a 500 as an empty catalogue.** `.catch(() => setIsLoading(false))` discards the error; the UI says "This shelf is still being stocked" and offers no retry. | `Movies.jsx:46` + 8 more | The retry path (`props.error` → "Projection interrupted" + RetryButton) **exists in `MoviesAndSeriesSections:83-107` and is passed by zero callers**. Good code, unreachable. |
| **6** | **"Watch online" CTA links to a route that does not exist.** `/watch/movie/...` → catch-all → 404. | `QualityTab.jsx:67-69,100-107` | The most prominent action on every file card dead-ends. |
| **7** | **Series quality filter is inverted** — `if (q.quality !== "480p" && !== "720p" && !== "1080p")` drops exactly the common qualities. | `MoviesAndSeriesDetailsSections.jsx:135-137` | `QualityTab` returns `null` when the list is empty, so any season whose files are all 1080p loses the **entire** download/Telegram/subtitle block. Looks like leftover debug code. |
| **8** | **Dead stream = black box.** No `error` listener on the Plyr instance or `<video>`. | `InlinePlayer.jsx:379-384`, `Watch.jsx:409-413` | The #1 failure mode on a streaming site gets zero user feedback and no retry. |
| **9** | **Watch popup renders an empty panel when `sources.length === 0`.** | `Watch.jsx:409-413` | Modal opens with only Close/Minimize over a black panel — no explanation. |

---

## 4. P1 — High impact (41, top 16)

**Navigation & shell**
- **`md: 700px` switches on a desktop header that needs ~990px.** Budget: logo 164 + 5 icon+label links 497 + My List 40 + `w-52` input 208 + gaps 24 + `px-8` 64 ≈ 1000px. Between 700–990px the nav compresses into the search input, and `focus:w-96` makes it worse mid-interaction. (`tailwind.config.mjs:18`, `Nav.jsx:326-355`)
- **Mobile search overlay + resize past 700px = permanently unscrollable page.** `body{overflow:hidden}` stays set after the overlay becomes `display:none`. Rotate a tablet and the page is dead until reload. (`Nav.jsx:237-241`)
- **`navStatus` string matching is wrong both ways.** `/search/oppenheimer` starts with `/ser` → the **Series** tab lights up on search results. `similarSeries`, `top-rated-*`, `most-viewed`, `genre/*`, `my-list`, `Cast/*` get **no** active tab at all. (`Nav.jsx:179-187`)
- **No `<main>` landmark, no skip link.** 2 `<main>` tags across 107 modules, neither on a public route. 12 navs + 18 headers to tab through, no content target. Desktop nav also has no `aria-current`. (`App.jsx:61`)
- **`html{scroll-behavior:smooth}` makes every route change animate** a smooth scroll of the *old* page. (`ScrollManager.jsx:77`, `index.css:25`)

**Correctness**
- **Duplicate homepage shelves.** `popular-movies` and `top-rated-movies` send byte-identical params; `apiCache` collapses them to one promise → the same 20 posters render twice under two headings. Same for the TV pair; ≥6 of 24 sections are duplicates or subsets. (`sectionsConfig.js:150-156` vs `166-172`)
- **Genre filtering applied twice with different rules.** `Movies.jsx` re-filters on **only `genresArr[0]`**, silently dropping titles whose genre is second. `GenrePage` filters correctly but keeps the unfiltered `total_count`, so Pagination advertises empty pages. (`Movies.jsx:33-44`)
- **Filters and page number never reach the URL.** Back, refresh, share all reset to page 1 unfiltered; "See all" links drop the filter they promise. (`Movies.jsx`, `SearchResults.jsx:113`, `CastMedia.jsx:122`, `SimilarMov.jsx:57`)
- **"Picked for you" can never activate.** `trackVisit()` writes `fc_watch_history`; `usePersonalizedRecommendations.js:12` reads `watchHistory`. The badge is permanently dead. (`utils/watchHistory.js:55`)
- **Anime pagination discards ~half of every page.** Backend pages movies and TV separately then `combined[:limit]`, while `total_count` counts the whole library → titles are skipped entirely.
- **Same `/api/id/:id` fetched twice per series**, and the season fetch has no cancellation → rapid season taps race and can show the wrong season. (`TvDetails.jsx:63-91,116-128`)
- **Playing an episode writes nothing to the URL** — no deep link, no back/forward, refresh loses the episode. (`MoviesAndSeriesDetailsSections.jsx:203-211`)
- **No "next episode" affordance anywhere.** No `listeners.ended`, no Next button, no auto-advance. On a binge-watching product this is the biggest missing interaction. (`.jsx:664-834`)

**Accessibility**
- **Contrast, measured not eyeballed.** On `#08090B`: `bone/35` = **2.78:1**, `/40` = **3.36:1**, `/25` = 1.79:1. AA threshold lands at **`bone/49`** — so `/45` and below fail everywhere; `/55` = 5.05 on ink but only **4.50 on `#14151A` cards** (borderline). **255 failing instances.** Worst files: `Nav.jsx` (20), `ManualAddPage` (15), `MoviesAndSeriesDetailsSections` (13), `MobileSearch` (11).
- **Focus rings are actively deleted.** `CastSection.jsx:66-67` sets `focus-visible:outline-none`, overriding the global `*:focus-visible` rule — every cast link is unfocusable for keyboard users. 13 `outline-none` vs 5 `focus-visible` app-wide.
- **Mobile search overlay is not a dialog** — no `role`/`aria-modal`, no Escape, no focus trap, no focus restore; two icon buttons unlabelled. Same gaps in `Watch` modal, `TrailerModal`, `PWAInstallPrompt`.

**Performance / SEO**
- **`/api/` cached `NetworkFirst` for 24h** with a 4s timeout → yesterday's catalogue and yesterday's **playback URLs** (dead links that look like player bugs). Fights the in-memory 60s cache; app-resume clears the 60s one while the SW serves the 24h one, so "refresh" does nothing. (`vite.config.js:125-133`)
- **No Open Graph / Twitter tags in static HTML** — social unfurlers don't run JS, so every share is a bare URL. Worse, the `og:image` default is an **SVG**, which Twitter/Facebook/LinkedIn reject. (`index.html`, `SEO.jsx:22`)
- **Every page ships `<meta name="robots" content="index, follow">`**, including search results and cast pages → thin duplicates offered to crawlers. No JSON-LD structured data despite `type="video.movie"` being passed.

---

## 5. P2 themes (90 findings — grouped)

**The design system was never enforced.** This is the highest-leverage cleanup:

| Dimension | Measured sprawl | Recommended target |
|---|---|---|
| Arbitrary font sizes | **299** occurrences, 15 distinct sizes; **56% below 12px** (`text-[10px]` alone = 149) | 5 semantic tokens with paired line-heights |
| `bone` opacity | **49** distinct steps; borders alone use 12 (`/5 /6 /8 /10 /12 /15 /18 /20 /25 /30 /35 /60`) | 5 text + 4 bg + 3 border |
| Border radius | **18** tokens / 310 uses; `rounded-[3px]` 79 vs `rounded` 4 (same value, two spellings); `borderRadius.4xl` defined, used **0** times | 3 steps: `sm 3px / md 8px / lg 16px` |
| Raw hex in JSX | **75** occurrences, 15 values | `#F0CA98` (21×) is the worst — it equals `--projector-bright`, a CSS var **never promoted to a Tailwind colour**, so `hover:bg-[#F0CA98]` has no utility alternative. Fixing the token removes 21 of 75. |
| Section headers | **3** inconsistent variants | one `<SectionHeader>` |
| Poster cards | **4** forked implementations; only `MovieCard` is correct | one `<PosterCard>` |
| Near-identical pages | 7 list pages + `SearchResults`/`Similar*`/`GenrePage` all reimplement fetch+pagination | one `<MediaListPage>` |

**Root causes worth naming** (fix these and the sprawl stops regenerating):
- `tailwind.config.mjs:59-74` redefines `fontSize` as a **flat** scale with **no `lineHeight` pairs**. That single omission is why the JSX is littered with manual `leading-snug`/`leading-tight` — each one is a patch for a missing pair. It also silently sets `text-sm` = 12.8px, below the comfortable-reading floor.
- `index.css:17-21` runs a hand-written `* { margin:0; padding:0 }` **after** Tailwind Preflight, beating it and stripping the intentional heading/paragraph margins — which is why every `<p>` and `<h*>` carries manual `mb-*`.
- `index.css` defines `.skeleton`, `.loader-episode`, `.plyr`, `.no-scrollbar`, `.eyebrow`, `.animate-float`, `.animate-glow-pulse` **twice each**; the later rule wins and orphans the earlier one's work (`@keyframes shimmer` is dead; the elaborate episode spinner is overridden by a plain one).
- `index.css:42-79` puts `!important` on inputs, which **outranks inline styles** — so `Nav.jsx:558`'s `backgroundColor: rgba(255,255,255,0.05)` is dead code. That is why the mobile search field looks like a black box on black.

**Missing UX fundamentals**
- No "next episode" / auto-advance; no loading→error distinction anywhere; no empty-vs-failed distinction.
- Hover-only affordances (play button, gradient) unreachable by touch and keyboard (P2-14).
- Touch targets below 44px across carousels and pagination.
- `prefers-reduced-motion` respected in CSS but escaped by framer-motion in 5 of 6 components; `NotFound`'s Lottie loops regardless.
- Episode thumbnails are eager `<img>` — up to 50 full-size backdrops per series page.
- Pagination is not a landmark and doesn't expose the current page; `aria-current` misused on hero reel buttons.
- Section order depends on 24+ concurrent `IntersectionObserver` fetches because collapsed lazy sections have no reserved height (P1-7 in discovery).

---

## 6. Dead code — the cheapest win available

**17 modules imported by nothing**, plus 5 dead auth modules. Deletion is zero-risk and removes:

- ~**2,300 lines** of source.
- **313 lines** of `index.css` (the entire `.audio-track-*` / `.subtitle-*` block) that exists only to serve the dead "mobile v2" stack.
- An entire **second undefined token system** — `--text-muted`, `--bg-surface`, `--bg-card`, `--border-color`, `--accent-primary` etc., referenced ~**150 times** with **zero definitions** in `index.css`. `bg-[var(--bg-primary)]` resolves to nothing; borders fall back to `currentColor`. It only renders "fine" because the pages are unreachable.

Confirmed unreachable: `MobileDownloads.jsx`, `MobileProfile.jsx`, `MobileSearch.jsx`, `MobileMovieDetails.jsx`, `BottomNav.jsx`, `MobileBottomNav.jsx`, `NotificationSystem.jsx`, `PullToRefresh.jsx`, `PageTransition.jsx`, `ContinueWatching.jsx`, `GenreSection.jsx`, `Login.jsx`, `Token.jsx`, `Logout.jsx`, `signInWIthGoogle.jsx`, `PrivateRoute.jsx`.

Also dead: `Watch` modal mounts on every movie page but `setIsWatchMoviePopupOpen(true)` is **never called** (419-line Plyr component passively running effects). And `OfflineFallback.jsx` — 142 lines of designed offline UI — is unreachable while the `pwa:offline-ready` event has no listener.

---

## 7. Recommended order of work

Sequenced so each step makes the next cheaper. **Do not start with the token sweep** — it's the largest mechanical diff and will mask everything else.

### Phase 0 — Stop the bleeding (½–1 day)
1. **Add an error boundary** (root + per-route) and coerce `rating` with `Number()`. *(P0-1)*
2. **Fix `vendor-react` splitting** with the `manualChunks(id)` resolver form, and lazy-load the detail/player routes. *(P0-2, P0-3)* — verify `vendor-react-*.js` is ~140 KB and appears in modulepreload.
3. **Delete the 17 dead modules** + dead auth files. *(§6)* Unblocks the token work.

### Phase 1 — Make failure visible (2–3 days) · *highest value*
4. Replace `safe()→[]` with an `{ok, data}` sentinel; thread `error`/`onRetry` into `MoviesAndSeriesSections` from all nine pages (the retry UI already exists). *(P0-4, P0-5)*
5. Give the hero a fallback on **any** error, and keep its box reserved. *(P0-4)*
6. Add a player `error` listener + retry overlay to `InlinePlayer` and `Watch`; render a "no playable source" state. *(P0-8, P0-9)*
7. Fix the two dead primary actions: `/watch/*` route and the inverted series-quality filter. *(P0-6, P0-7)*

### Phase 2 — Correctness the user can feel (3–5 days)
8. Move `page`, `sort`, season, episode and search term into `useSearchParams`. Fixes deep links, Back, and gives SEO a canonical in one change.
9. Add "Next episode" / auto-advance and an `onEnded` listener.
10. De-duplicate `SECTIONS` (with a startup assert on identical params); fix anime pagination; unify the two watch-history storage keys.
11. Delete the divergent client-side genre re-filters so counts and contents agree.

### Phase 3 — Consolidate the design system (3–5 days)
12. Promote `--projector-bright` to a Tailwind colour (kills 21 raw hex), publish the 5/4/3 opacity ramp, the 3-step radius scale, and fix `fontSize` to the **tuple** form with line-heights — then remove `md` (it collides with the breakpoint).
13. Set `md: 768px` for shell parity, make the header fluid below `lg`, and keep the full desktop nav on an honest breakpoint.
14. Extract `<PosterCard>`, `<SectionHeader>`, `<MediaListPage>`, `<EmptyState>`; migrate the 4 card / 3 header / 8 page call sites.
15. Delete the `* { margin:0; padding:0 }` reset and the duplicated CSS rules (`.skeleton` etc.).
16. *Last:* codemod `text-[Npx]` → scale tokens. Expect a visual diff.

### Phase 4 — Accessibility & platform (2–3 days)
17. Raise the text floor to `bone/60` for anything under 14px (6.14:1 ink / 5.48:1 carbon); restore deleted focus rings; add `<main id="main">` + skip link + `aria-current` on desktop nav.
18. Give every overlay real dialog semantics (role, aria-modal, Escape, focus trap, focus restore, `aria-label` on icon buttons).
19. Replace `body{overflow:hidden}` with `position:fixed` scroll-lock (iOS), and close the mobile search overlay on breakpoint cross.
20. Add static OG/Twitter tags + a 1200×630 PNG default (not SVG); add `noIndex` for search/cast/404; add JSON-LD.
21. Drop the SW `/api/` TTL to ~5 min and `NetworkOnly` the volatile endpoints (`hero`, `quality`, `sources`).
22. Gate framer-motion animations on `useReducedMotion()`; add `loading="lazy"` to episode thumbnails.

---

## 8. Quick reference — the 10 highest-value single-line-ish fixes

| Fix | File |
|---|---|
| `Number(result.rating)` guard | `Nav.jsx:55,106` |
| `if (!debouncedVal) { setSearchResult([]); setIsLoading(false); return; }` — fixes infinite "Searching…" | `Nav.jsx:191-194` |
| `encodeURIComponent(term)` in both `handleSeeAll` paths — fixes `/` in titles breaking routing | `Nav.jsx:276,282` |
| Delete `focus-visible:outline-none` from cast links | `CastSection.jsx:66` |
| `window.scrollTo({ top: 0, behavior: "instant" })` — kills smooth-scroll on navigation | `ScrollManager.jsx:77` |
| `<main id="main">` instead of `<div>` | `App.jsx:61` |
| `.catch((e) => { setError(e); setIsLoading(false); })` + pass `error`/`onRetry` | 9 list pages |
| Remove the `!== "480p" && !== "720p" && !== "1080p"` inversion | `.jsx:135-137` |
| Point the CTA at the in-page player instead of `/watch/...` | `QualityTab.jsx:67` |
| Move `<meta charset>` to line 4 (before all scripts) | `index.html:34` |
