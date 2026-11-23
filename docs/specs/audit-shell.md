# UI/UX Audit — App Shell, Navigation, Theme Config

Scope: `src/App.jsx`, `src/main.jsx`, `src/components/{Nav,Footer,BottomNav,MobileBottomNav,ScrollManager,PageTransition,SplashScreen,PullToRefresh,NotificationSystem}.jsx`, `src/config/sectionsConfig.js`, `tailwind.config.mjs`, with `src/index.css` and `index.html` as context.
No source files were modified.

## Two corrections to the brief (verified, not assumed)

1. **Out-of-order `screens` keys do *not* produce out-of-order CSS.** Tailwind 3.4.19 sorts breakpoints by numeric value at build time. I compiled the real config and the emitted media queries are in ascending order: `360, 400, 480, 500, 700, 750, 920, 1024, 1224, 1440, 1536, 1600`. Cascade order is therefore *correct*; the declaration order is a readability/trap hazard, not a rendering bug.
2. **There is no "leftover 768px" from Tailwind.** `screens` is replaced wholesale (not inside `extend`), so no default `768px` block is emitted. The `768px` that *does* exist comes from hand-written `@media (max-width: 768px)` / `640px` rules in `src/index.css` — which no longer line up with `md: 700px` / `sm: 480px`. That mismatch is the real finding (see "hand-written `max-width` queries" under P2).

`md: 700px` is nonetheless a genuine, load-bearing bug: `md:` is the desktop/mobile breakpoint for the entire shell, and 700px is below the width at which the desktop nav actually fits (P1, "`md: 700px` switches on a desktop header").

---

## P0 — Blocker

- **[P0] `src/App.jsx:34-40` — no error boundary anywhere in the tree; a single render throw blanks the whole product.**
  `createRoot(...).render(<StrictMode><NextUIProvider><HelmetProvider><App/>…` and `Shell()` render `<Nav/>` + `<Routes/>` with no boundary. PWA users get a permanently white screen (no nav, no footer, no "reload" affordance) and must hard-refresh. This is reachable from ordinary data, not just dev mistakes. Concrete throw site: `src/components/Nav.jsx:55` / `:106` call `result.rating?.toFixed(1)`. The optional chain guards `null`/`undefined` but **not a string**: Mongo stores `rating` untyped (`Backend/helper/database.py:1980-1987` just projects whatever is in the document), so any document whose rating was written as `"7.5"` renders `TypeError: result.rating.toFixed is not a function` the moment it appears in the search dropdown — and there is no boundary to contain it.
  Fix: add a root boundary plus a route-level boundary, and coerce the number.
  ```jsx
  // src/components/ErrorBoundary.jsx
  class ErrorBoundary extends React.Component {
    state = { error: null };
    static getDerivedStateFromError(error) { return { error }; }
    componentDidCatch(e, info) { console.error("Shell crash", e, info); }
    render() {
      if (!this.state.error) return this.props.children;
      return (
        <div role="alert" className="min-h-[60vh] grid place-items-center px-6 text-center">
          <div>
            <h1 className="font-display text-2xl text-bone">Something broke in the projector</h1>
            <button type="button" onClick={() => location.reload()} className="btn-primary mt-5">Reload</button>
          </div>
        </div>
      );
    }
  }
  // App.jsx: wrap <Shell /> inside <BrowserRouter>, and wrap <Routes> inside the content div
  ```
  ```jsx
  // Nav.jsx:55 and Nav.jsx:106
  const r = Number(result.rating);
  {Number.isFinite(r) && r > 0 && <span …>{r.toFixed(1)}</span>}
  ```

---

## P1 — High

- **[P1] `src/components/Nav.jsx:518` + `:237-241` — resizing above 700px while the mobile search is open leaves the page permanently unscrollable.**
  The overlay is `fixed inset-0 z-[60] md:hidden`, but `isMobileSearchOpen` stays `true` and the lock effect only depends on that state:
  ```js
  document.body.style.overflow = isMobileSearchOpen ? "hidden" : "";
  ```
  Rotate a tablet / drag a desktop window across 700px and the overlay becomes `display:none` while `body{overflow:hidden}` remains → no visible UI, no way to close, scroll dead until reload. (Same class of trap in `PWAInstallPrompt` is avoided only because it has no scroll lock.)
  Fix: close the overlay when the breakpoint is crossed, using the same 700px the class uses.
  ```js
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 700px)"); // keep in sync with tailwind md
    const onChange = (e) => { if (e.matches) { setIsMobileSearchOpen(false); clearSearch(); } };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [clearSearch]);
  ```

- **[P1] `src/components/Nav.jsx:179-187` — `navStatus` string matching is wrong in both directions.**
  ```js
  else if (path.startsWith("/mov") || path.startsWith("/Movies")) setNavStatus("Movies");
  else if (path.startsWith("/ser") || path.startsWith("/Series")) setNavStatus("Series");
  ```
  False positive: `/search/oppenheimer` starts with `/ser` → **the "Series" tab lights up while the user is on a search results page**. Also `/similarSeries/:id` and `/similarMov/:id` do *not* start with `/ser`/`/mov`, so they get no active tab. Missing active state entirely for `/top-rated-movies`, `/top-rated-series`, `/most-viewed`, `/genre/:mediaType/:genreName`, `/my-list`, `/Cast/:castId/:castName` (all fall through to `setNavStatus("")`, i.e. the dock shows no active pill).
  Fix: route-prefix map, longest-match-first, no bare `startsWith` on 3 letters.
  ```js
  const ROUTE_TAB = [
    ["/Movies", "Movies"], ["/top-rated-movies", "Movies"], ["/most-viewed", "Movies"],
    ["/genre/movie", "Movies"], ["/mov/", "Movies"], ["/similarMov/", "Movies"],
    ["/Series", "Series"], ["/top-rated-series", "Series"], ["/genre/tv", "Series"],
    ["/ser/", "Series"], ["/similarSeries/", "Series"],
    ["/anime", "Anime"], ["/k-drama", "K-Drama"], ["/my-list", "My List"],
  ];
  const navStatus = path === "/" ? "Home"
    : (ROUTE_TAB.find(([p]) => path === p || path.startsWith(p + "/") || path === p)?.[1] ?? "");
  // derive with useMemo(pathname) instead of useState + effect (P3, "derived state mirrored through an effect")
  ```

- **[P1] `src/components/Nav.jsx:511-572` — the mobile search overlay is not a dialog: no Escape handler, no focus trap, no focus restore, no `role`/`aria-modal`, no `aria-label` on two icon buttons.**
  `grep` for `keydown|Escape|onKeyDown|role="dialog"|aria-modal` in `Nav.jsx` returns nothing. Consequences: (a) on a keyboard/switch device Tab walks into the page *behind* the overlay (the dock at `:670` and the nav links are still in the tab order because the overlay is a sibling, not a portal); (b) Escape does nothing; (c) after `setIsMobileSearchOpen(false)` focus falls to `<body>`, so the next Tab starts at the top of the document; (d) the back chevron (`:529`) and the clear "×" (`:562`) are icon-only with no `aria-label`.
  Fix:
  ```jsx
  const overlayRef = useRef(null);
  const triggerRef = useRef(null);
  useEffect(() => {
    if (!isMobileSearchOpen) return;
    const onKey = (e) => { if (e.key === "Escape") { setIsMobileSearchOpen(false); clearSearch(); triggerRef.current?.focus(); } };
    document.addEventListener("keydown", onKey);
    A11Y_HIDDEN.forEach((el) => el.setAttribute("inert", "")); // nav bar + dock + #root content
    return () => { document.removeEventListener("keydown", onKey); A11Y_HIDDEN.forEach((el) => el.removeAttribute("inert")); };
  }, [isMobileSearchOpen, clearSearch]);
  // <motion.div role="dialog" aria-modal="true" aria-label="Search" ref={overlayRef} …>
  // <button aria-label="Close search" …>  <button aria-label="Clear search" …>
  ```

- **[P1] `tailwind.config.mjs:18` + `src/components/Nav.jsx:326-355` + `src/App.jsx:61` — `md: 700px` switches on a desktop header that needs ≈990px; 700–990px clips/overflows.**
  With `md` redefined to 700 (default is 768), the full desktop header (`hidden md:flex`, logo + 5 icon+label links + 40px My List + `w-52` input, `md:px-8`) appears at 700px. Rough budget at 700px: logo ≈164 + 5 items ≈497 + My List 40 + gaps 8 + input 208 + `gap-3`×2 24 + `px-8` 64 ≈ **1000px**. Below ~990px the flex row has no room: the `w-52` input cannot shrink, the nav items compress and the labels wrap/overlap the input. `focus:w-96` (`:409`) makes it worse mid-interaction (input grows to 384px on focus → layout shift while typing). Independently of the value, raising the breakpoint does not fix the density.
  Fix (pick one): keep `md` at 768 and gate the full nav on a wider, honest breakpoint; or make the header fluid — collapse labels below `lg`, let the input flex.
  ```jsx
  // icon-only labels until lg, fluid input
  <span className="hidden lg:inline">{item.name}</span>
  <input className="w-40 lg:w-72 xl:w-80 focus:w-44 lg:focus:w-80" />
  // and tailwind.config.mjs: md should be 768 for shell parity with index.css (see "hand-written max-width queries" under P2)
  ```

- **[P1] `src/components/ScrollManager.jsx:77` + `src/index.css:25` — every client-side navigation animates a smooth scroll to top instead of jumping.**
  `html { scroll-behavior: smooth }` applies to programmatic `window.scrollTo(0, 0)`. So every route change (Home → /Movies) plays a ~300–600ms smooth scroll *of the old page* before the new content is read from the top; on a long feed the new page appears already scrolled and slides up. The same applies to the 150-frame restore loop at `:58-64`, where each allocation of a smooth scroll cancels/queues the next.
  Fix: force instant for programmatic scrolls.
  ```js
  window.scrollTo({ top: 0, left: 0, behavior: "instant" });   // :77
  window.scrollTo({ top: target, behavior: "instant" });        // :59
  ```

- **[P1] `src/config/sectionsConfig.js:151-156` vs `:167-172` and `:160-165` vs `:173-179` — the homepage renders the same rows twice.**
  `popular-movies` (`{sort_by:"rating:desc"}`) and `top-rated-movies` (`{sort_by:"rating:desc"}`) issue byte-identical requests; `getJSON` caches on `path + JSON.stringify(params)` (`src/utils/apiCache.js:8`), so the second one is a cache hit and returns **exactly the same 20 movies** — the user scrolls past "Popular Movies" and then "Top Rated Movies" with identical posters in identical order. Same for `popular-tv` / `top-rated-tv`. `international` (`:373`, `page:4`, rating desc) and `recommended` (`:360`, `page:2`, rating desc) are also just later pages of the same ranking.
  Fix: differentiate the queries or drop the duplicate sections; at minimum make the ranking operator differ.
  ```js
  { id: "popular-movies", fetch: (B) => fetchMovies(B, { sort_by: "view_count:desc" }) },
  { id: "top-rated-movies", fetch: (B) => fetchMovies(B, { sort_by: "rating:desc", min_rating: 7 }) },
  ```

- **[P1] Contrast failure on body copy across the shell — measured, not eyeballed.**
  `bone` = `#F2EDE3` on `ink` = `#08090B`: `bone/35` → **2.84:1**, `bone/40` → **3.38:1**, `bone/30` → 2.37:1, `bone/25` → 1.98:1 (4.5:1 required for body text, 3:1 for ≥18.66px bold / 24px). `bone/50` = 4.72:1 (pass), `bone/55` = 5.51:1 (pass). Concrete offenders in scope:
  - `src/components/Nav.jsx:59`, `:110`, `:114` (search result metadata + synopsis, 10–11px), `:444`, `:484`, `:583`, `:619`, `:647` (section eyebrows at 10px), `:611` ("Try a different search term"), `:639`, and 9 more.
  - `src/components/Footer.jsx:129` (`text-bone/30`, copyright), `:132` (`text-bone/25`, tagline), `:91`/`:108` (`text-bone/40` headings).
  - `src/components/SplashScreen.jsx:98` (`text-bone/35`), `:113` (`text-bone/40`) — 9px text at 2.84:1 is effectively invisible.
  Fix: raise the floor. Anything carrying information should be `bone/60`+ on `ink` (6.4:1); reserve `/35`–`/45` for hairlines and decorative dividers only. Codify it so it can't regress:
  ```js
  // tailwind.config.mjs — add semantic tokens instead of raw opacity steps
  colors: { "bone-muted": "#A8A49B", /* 8.0:1 */ "bone-subtle": "#8A8780" /* 6.1:1 */ }
  ```

- **[P1] `src/App.jsx:58-81` — no `<main>` landmark, no skip link, and the desktop nav exposes no current page to AT.**
  The route outlet is a bare `<div className="min-h-screen bg-ink">`. With 5 nav links + My List + search + footer, a keyboard/AT user must Tab through the entire header on every navigation, with no "Skip to content" and no `<main>` to jump to. The mobile dock does set `aria-current` (`Nav.jsx:687`) but the desktop list (`:326-355`) does not, so the only current-page signal on desktop is a 1px gold underline.
  Fix:
  ```jsx
  <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[100] focus:rounded focus:bg-projector focus:px-4 focus:py-2 focus:text-ink">Skip to content</a>
  <Nav />
  <main id="main" tabIndex={-1} className="pt-[calc(var(--safe-top)+4rem)] …">…</main>
  // Nav.jsx desktop link:
  aria-current={isActive ? "page" : undefined}
  ```

---

## P2 — Medium

- **[P2] `src/index.css:42-79` (3 duplicated blocks) defeats every input utility and even the inline style it fights — `src/components/Nav.jsx:554-559`.**
  ```css
  input, textarea, select { background-color: var(--carbon) !important; }
  ```
  This overrides Tailwind classes (a `bg-bone/5` on an input silently does nothing) **and** beats an inline style, because `!important` in a stylesheet outranks a normal declaration in a `style` attribute. That is exactly what happens in the mobile search field: `style={{ backgroundColor: "rgba(255,255,255,0.05)", color: "#F2EDE3" }}` is dead code — the field renders `#14151A`, which is why it looks like a black box on a black overlay. Also `Nav.jsx:408` `placeholder-bone/40` is overridden by `input::placeholder { … !important }` (same value, harmless, but it means the class is decorative).
  Fix: delete the `!important` block, scope it, and use a token class.
  ```css
  @layer components {
    .field-dark { background-color: var(--carbon); color: var(--bone); caret-color: var(--projector); }
    .field-dark::placeholder { color: rgb(242 237 227 / 0.55); opacity: 1; }
  }
  ```

- **[P2] `src/components/Nav.jsx:237-241` — `body{overflow:hidden}` is not a scroll lock on iOS Safari.**
  On iOS the background still scrolls under the overlay (rubber-band + the page behind moves), and because `scrollY` changes while the overlay is open, `ScrollManager` persists a garbage position for the previous route. `body { overflow-x: hidden }` (`index.css:34`) also makes `body` a scroll container, further degrading the interaction.
  Fix: freeze with `position:fixed` + restore scroll on close.
  ```js
  useEffect(() => {
    if (!isMobileSearchOpen) return;
    const y = window.scrollY;
    Object.assign(document.body.style, { position: "fixed", top: `-${y}px`, left: 0, right: 0, width: "100%" });
    return () => {
      Object.assign(document.body.style, { position: "", top: "", left: "", right: "", width: "" });
      window.scrollTo({ top: y, behavior: "instant" });
    };
  }, [isMobileSearchOpen]);
  ```

- **[P2] `src/components/Nav.jsx:190-204` — clearing the query mid-flight leaves `isLoading` stuck `true` forever, and the dropdown shows a spinner instead of Recent.**
  ```js
  if (!debouncedVal) { setSearchResult([]); return; }   // :191-194 — never setIsLoading(false)
  ```
  Type "dune", then hit the "×" (`clearSearch` → `debouncedVal = ""`) before the request resolves. `setIsLoading(false)` only exists in the `then`/`catch` of a request whose effect has been aborted, so the flag stays `true`. Refocus the (now empty) input → `recentSearches.length > 0` renders the dropdown → `isLoading ? <spinner/>` → infinite "Searching…" (`:436-440`). Same state is shared with the mobile overlay, so the mobile empty state also shows a spinner forever.
  Fix:
  ```js
  if (!debouncedVal) { setSearchResult([]); setIsLoading(false); return; }
  ```
  Also guard non-2xx: `fetch` resolves on 500, `r.json()` then throws on HTML error pages (currently swallowed by `.catch` with no user-visible error state at all — there is no `error` branch in either dropdown, so a backend outage is indistinguishable from "No results").

- **[P2] `src/components/Nav.jsx:113-115` — the mobile result synopsis reads a field the API never returns, so it never renders.**
  ```jsx
  {result.overview && <p className="…">{result.overview}</p>}
  ```
  The `/api/search/` projection (`Backend/helper/database.py:1979-1988`) returns `description`, not `overview`. So `result.overview` is always `undefined`, the block is dead, and the mobile result card loses the one line of context that distinguishes two same-titled entries (e.g. remakes). The `rating`/`release_year` siblings in the same card are correct.
  Fix: `{result.description && <p …>{result.description}</p>}` (optionally `lineClamp` 2 on the parent), or normalise both shapes in `getJSON`.

- **[P2] `src/components/Nav.jsx:274-285` — search targets are not URL-encoded; terms containing `/`, `#`, `%`, `?` break routing.**
  `navigate(\`/search/${term}\`)` with `term = "Spider-Man / No Way Home"` produces `/search/Spider-Man / No Way Home`; the `/search/:searchResult` param stops at the slash, so the user lands on `/search/Spider-Man` (wrong results) or, for a term starting with `/`, on the 404 route (`App.jsx:79`). Terms come straight from raw input (`:551`).
  Fix: `navigate(\`/search/${encodeURIComponent(term.trim())}\`)` in both `handleSeeAll` (`:276`) and `handleMobileSeeAll` (`:282`); decode in `SearchResults`.

- **[P2] `src/components/ScrollManager.jsx:51-54` — back/forward to a history entry with no saved position does not reset scroll.**
  ```js
  if (navigationType === "POP") {
    const target = positions.current[location.key];
    if (!target) return undefined;     // ← no fallback
  ```
  `history.scrollRestoration = "manual"` is set at `:21-23`, so nothing else resets the viewport, and the new page is painted at the *previous page's* scroll offset — landing the user mid-grid on a page they've never scrolled. Also `if (!target)` treats a legitimate `0` as missing.
  Fix:
  ```js
  if (navigationType === "POP") {
    const target = positions.current[location.key];
    if (target == null) { window.scrollTo({ top: 0, behavior: "instant" }); return; }
  ```

- **[P2] `src/components/ScrollManager.jsx:56-66` — the restore loop can fight the user for 2.5s.**
  The `requestAnimationFrame` loop re-issues `scrollTo(0, target)` up to `MAX_RESTORE_FRAMES = 150` while `|scrollY - target| > 2`. If the destination is shorter than `target` (content still loading — which is the normal case here, since every section is a separate async fetch) the condition never resolves and the page fights any user scroll attempt for ~150 frames. There is no "user took over" bail-out.
  Fix: abort on first user input, and give up on a settled-target check rather than a fixed frame count.
  ```js
  const abort = () => cancelAnimationFrame(frame);
  ["wheel", "touchstart", "keydown"].forEach((t) => window.addEventListener(t, abort, { once: true, passive: true }));
  if (!stopped && Math.abs(window.scrollY - target) > 2 && frames < 40) frame = requestAnimationFrame(restore);
  ```

- **[P2] `src/components/SplashScreen.jsx:4-5`, `:34` + `src/pages/Home.jsx:98` — the splash re-plays on every visit to Home, not just cold start, and can block for 3s.**
  `SplashScreen` lives inside `Home`, so returning to `/` from any detail page remounts it: `phase` resets to `"visible"`, `MIN_VISIBLE_MS = 400` applies even on a warm cache, `MAX_VISIBLE_MS = 3000` applies when `ready` never flips. It is `fixed inset-0 z-[9999]`, so it swallows all pointer input and also covers the PWA prompt and toasts. There is no session guard and no way to dismiss.
  Fix: show once per session, and never longer than ~700ms on a warm load.
  ```js
  const seen = sessionStorage.getItem("fc:splash") === "1";
  // in Home: {!seen && <SplashScreen ready={!heroLoading} onDone={() => sessionStorage.setItem("fc:splash","1")} />}
  // SplashScreen: use MAX_VISIBLE_MS = 1200 and add onClick={skip} / "Skip" button
  ```

- **[P2] `src/components/Footer.jsx:95-102` — "Content" chips are `<span>`s styled as controls; they hover, they lift, they do nothing.**
  `hover:text-bone/90 hover:bg-bone/8 transition-colors cursor-default` on a non-interactive `<span>`. On touch, the hover state sticks after tap, so users tap a chip, it lights up, and nothing navigates — a false affordance for the six most valuable SEO/UX destinations on the page. Contrast is also marginal inside the chips (`text-bone/55` on `bg-bone/5` over `#0B0C10` ≈ 5.1:1, ok, but the `bg-bone/5` fill pushes it under any hover overlay).
  Fix: make them real links to the genre routes that already exist (`/genre/movie/Action`, `/anime`, `/k-drama`, …) and drop `cursor-default`.

- **[P2] `src/config/sectionsConfig.js:105-375` + `src/components/SmartSection.jsx:46-50` — 7 sections fire in parallel on first paint and 38 sections total are declared.**
  All of `trending-now, continue-watching, new-episodes, most-watched, latest-movies, latest-tv, popular-movies` are `priority: "high"`, and `SmartSection` calls `doFetch()` immediately for each — 7 concurrent requests before the hero is even interactive (the hero and Top-10 are two more: `Home.jsx:70`, `:79`). Of the **35** declared sections (`grep -c '^    id: "'` → 35), several resolve to the same cached request (see the duplicate-sections finding above) and `recently-added` / `recommended` each issue **two** requests. On a 3G mobile device this is the main-thread contention that makes the first scroll janky.
  Fix: fetch through a small concurrency limiter (2–3 at a time) and demote everything below `most-watched` to `"lazy"`; the sentinel is already positioned correctly.

- **[P2] `tailwind.config.mjs:59-74` — overriding `fontSize` with bare strings drops every paired `line-height`.**
  Compiled output confirms only `font-size` is emitted (`text-base → font-size: 1rem` with no `line-height`), so every heading inherits `body`'s line-height. `text-3xl` (1.953rem) and `text-4xl` (2.441rem) therefore render at ~1.5 leading (~59px for 4xl) unless a component happens to add `leading-*`. It also silently shrinks the default scale (Tailwind `sm` is 0.875rem; here 0.8rem) and adds a non-standard `md` step — so `text-sm` body copy is smaller *and* looser than the design system implies, which compounds the contrast failures in the P1 contrast finding.
  Fix: supply `[size, { lineHeight }]` tuples, or leave `fontSize` alone and only add the extra step.
  ```js
  fontSize: { md: ["0.9rem", { lineHeight: "1.5" }], "3xl": ["1.953rem", { lineHeight: "1.15" }], "4xl": ["2.441rem", { lineHeight: "1.1" }] }
  ```

- **[P2] `src/App.jsx:61` + `src/components/Nav.jsx:672-674, 712` — dock clearance is a magic number while the dock's own height is content-driven, and its labels are 9px fixed.**
  `pb-28` (112px) is tuned to today's `min-h-[54px]` + `p-1.5` dock. Add a second line of text, bump `min-h`, or run Android system font scaling and the dock grows past 112px and starts eating the last row of every page; conversely the `Footer` uses a *different* constant (`pb-[calc(var(--safe-bottom)+6.5rem)]`, `Footer.jsx:40`) for the same overlap. Meanwhile the labels are `text-[9px]` in `px`, so they ignore user font-size preferences entirely — the primary navigation on mobile is the least legible text on the site.
  Fix: one token, consumed by both.
  ```css
  :root { --dock-h: 4.25rem; }                      /* 68px */
  .pb-dock { padding-bottom: calc(var(--safe-bottom) + var(--dock-h) + 1rem); }
  ```
  ```jsx
  <div className="… pb-dock md:pb-10">              {/* App.jsx */}
  <footer className="… pb-dock md:pb-16">           {/* Footer.jsx */}
  <span className="timecode text-[0.6875rem] …">    {/* 11px, rem-based */}
  ```

- **[P2] Dead components shipped in the bundle — five files with zero importers.**
  `grep -r "BottomNav|MobileBottomNav|PageTransition|PullToRefresh|NotificationSystem" src` returns only their own definitions. Only `SplashScreen` is used (`Home.jsx:26`).
  - `src/components/BottomNav.jsx` (51 lines) — a **second** mobile dock: `fixed bottom-0 … z-50 md:hidden`, 4 tabs, wrong palette (`text-primaryBtn`, `gray-400`, `bg-black/90`) vs the brand tokens, `navStatus` never resets on unmatched routes (same bug as the `navStatus` finding above), `/search` link points at a route that does not exist (`App.jsx` only declares `/search/:searchResult`).
  - `src/components/MobileBottomNav.jsx` (81 lines) — a **third** dock: Netflix red `#E50914`, `gray-500` labels, `handleTabChange(id, path)` ignores `path`, `layoutId="activeTab"`. It is also the only one of the three that meets 44px (`min-w-[44px] min-h-[44px]`).
  - `src/components/PageTransition.jsx` — `AnimatePresence mode="sync"` keyed on `pathname`; if ever wired around `<Routes>`, both pages mount simultaneously and duplicate every fetch. Unused, so it is pure dead weight.
  - `src/components/PullToRefresh.jsx` — see the `PullToRefresh` finding below.
  - `src/components/NotificationSystem.jsx` — see the `NotificationSystem` finding below.
  Three parallel implementations of the primary navigation is the highest-risk dead code here: wiring the wrong one re-introduces a second fixed dock at `bottom-0` that the `pb-28` clearance does not account for.
  Fix: delete `BottomNav.jsx`, `MobileBottomNav.jsx`, `PageTransition.jsx` (or adopt it deliberately), and whichever of `PullToRefresh`/`NotificationSystem` is not about to be wired; keep the inline dock in `Nav.jsx` as the single source of truth (it is the only one with `aria-current` + haptics + `--safe-bottom`).

- **[P2] `src/components/PullToRefresh.jsx:20-27, 37-45, 81-82, 100-108` — dead *and* non-functional; do not wire it as-is.**
  - `containerRef.current.scrollTop` is always `0`: the container is `overflow-hidden`, the page scrolls on `window`, so the "only at the top" guard never rejects anything — the gesture arms anywhere on the page.
  - No `e.preventDefault()` / `passive: false`, with `touchAction: "pan-y"`, so the browser scrolls *and* the indicator moves: the content visibly travels twice as far as the finger.
  - `if (deltaY >= threshold && !canRefresh) medium()` reads `canRefresh` from the render closure, which is stale within a single gesture → the haptic repeats every `touchmove`.
  - No `onTouchCancel` → a cancelled gesture leaves `isPulling` stuck `true`, and the next `touchmove` anywhere drags the page down.
  - Uses `var(--accent-primary)`, `var(--bg-surface)`, `var(--text-muted)` — **none of these are defined** in `index.css` `:root` (only `--ink/--carbon/--bone/--projector/--signal`), so the pill renders with a transparent background and inherited text color.
  Fix: delete, or rewrite against `window.scrollY === 0`, `{ passive: false }` + `preventDefault()` past a 6px slop threshold, a `useRef` for threshold state, and the real tokens (`bg-carbon`, `text-bone/60`).

- **[P2] `src/components/NotificationSystem.jsx:11-12, 39-43, 109, 128, 139, 147, 159` — dead *and* inaccessible; a toast system with no live region.**
  - The container (`:109`) is a plain `<div className="fixed top-4 right-4 z-[100] …">` — no `role="status"`/`aria-live="polite"`, so notifications are never announced. Nothing focuses them either.
  - `id = Date.now()` (`:11`) collides when two notifications are added in the same millisecond → duplicate React keys → one toast silently fails to render.
  - The auto-dismiss `setTimeout` (`:40`) is never stored or cleared: unmount leaks the timer, and it then calls `removeNotification` on an unmounted tree.
  - `window.showNotification = addNotification` in an effect with `[]` deps (`:53-58`) captures the *first* render's closure; it works today only because the closure touches nothing but `setNotifications`. Any future state read inside `addNotification` will be permanently stale (this is the classic stale-closure bug). Wrap in `useCallback` and depend on it.
  - `min-w-[300px]` on a `fixed right-4` card (`:124`) overflows a 320px viewport; `body{overflow-x:hidden}` (`index.css:34`) clips it rather than scrolling, so the close button ends up partially off-screen.
  - Undefined `var(--text-secondary)` / `var(--text-muted)` (`:139`, `:147`) → message text inherits, close icon has no color change on hover.
  Fix: `role="status" aria-live="polite" aria-atomic="false"`, `crypto.randomUUID()`, store and clear timers, `w-[calc(100vw-2rem)] max-w-sm`.

- **[P2] `src/index.css:476, 637, 916, 1046` — hand-written `max-width: 640px` / `768px` media queries no longer align with the custom `screens` (see the `md: 700px` P1 finding).**
  Tailwind's `sm` is now 480 and `md` is 700, but `index.css` still branches at 640 and 768:
  - `:916-925` `.mobile-tap-target { min-height:44px }` only applies ≤768 — so between 700 and 768 the *desktop* header styles are active while the file still thinks it is mobile (harmless here, but it means the 44px rule silently outlives the mobile breakpoint).
  - `:1046-1050` plyr progress-bar tap target enlarges ≤768, i.e. across a band where the layout is already desktop.
  - `:476-...` / `:637-...` audio-track-selector rules reflow at 640 while the surrounding card grid switched to its 3-column layout at 480.
  Fix: express these as Tailwind screens instead of raw px, or move the raw values to match (`max-width: 479px`, `max-width: 699px`), so there is exactly one breakpoint definition in the project.

- **[P2] `src/index.css:829-833` — the global focus ring sets `border-radius` on every focused element, deforming the component.**
  ```css
  *:focus-visible { outline: 2px solid var(--projector); outline-offset: 2px; border-radius: 4px; }
  ```
  Focusing the mobile dock tab (rounded `15px`, `Nav.jsx:690`) or the My List circle (`rounded-full`, `:364`) snaps its radius to 4px while focused — visible layout jitter on Tab, on a nav that is otherwise full of intentional radii.
  Fix: drop `border-radius` from the global rule; `outline` already follows the element's own radius in every current browser.
  ```css
  *:focus-visible { outline: 2px solid var(--projector); outline-offset: 2px; }
  ```

- **[P2] `src/components/Nav.jsx:414-421`, `:562-570`, `:529-542` and `:632-660` — touch targets below 44px on the mobile-only surfaces.**
  - Desktop input clear: icon-only `FiX` at `right-3.5`, no width/height → ~16px hit box, no `aria-label`.
  - Mobile overlay clear: `w-6 h-6` = **24px**, no `aria-label` (`:562`).
  - Back chevron: `w-10 h-10` (40px) but the `<svg>` inside is 18px and the `<button>` has no `aria-label` (`:529-542`) — AT announces an unlabeled button.
  - Recent/Trending chips: `px-3.5 py-2` with `text-sm` → **34px tall** (`:637`, `:655`), the primary way to start a search on mobile.
  Fix: give icon buttons `h-11 w-11` (or add `after:absolute after:-inset-2.5` to expand the hit area without changing visuals), `aria-label` on all three, and `min-h-[44px]` on the chips.

---

## P3 — Nits

- **[P3] `src/App.jsx:17-19, 25, 27, 30` — six commented-out imports/config blocks left in the shell.**
  `// import Token`, `// import Login`, `// import PrivateRoute`, `// import { AuthProvider }`, `// import { ToastContainer } from "react-toastify"` — while `Home.jsx:99` *does* render `<ToastContainer>`. Either the toast container is half-migrated or the comment is misleading; pick one and delete the residue. Same for `src/App.jsx:57` (a stale comment about "fixed nav and mobile bottom bar" that predates the floating dock).

- **[P3] `src/components/Nav.jsx:129` + `:179-187` — `navStatus` is derived state mirrored through an effect.**
  `useState("Home")` + `useEffect` + optimistic `setNavStatus(item.name)` in three `onClick`s (`:307`, `:333`, `:694`). Two sources of truth for the same value; on a fast double-navigation the optimistic set and the effect disagree for a frame, and the dock pill visibly hops to the old tab before settling. Replace with `const navStatus = useMemo(() => tabForLocation(pathname), [pathname])` (see the `navStatus` P1 finding) and delete both `useState` and the effect.

- **[P3] `src/components/Nav.jsx:346-351` / `:699-704` — two `layoutId`s, and neither survives a breakpoint change.**
  `activeNavPill` and `mobileDockPill` are distinct (no cross-viewport flight today), but both live inside breakpoint-gated containers (`hidden md:flex` / `md:hidden`). Resizing across 700px unmounts the pill mid-spring, so Framer restarts the shared-layout measurement from scratch — visible as a one-frame flash of the pill at the new tab. If the desktop nav ever became visible at both breakpoints (e.g. removing `hidden md:flex` for a tablet label row), the single `layoutId` *would* animate the pill across the full viewport. Either keep the ids per-breakpoint and add `layout="position"`-free static styling below `lg`, or gate rendering on a `useMediaQuery` instead of CSS `display`.

- **[P3] `src/components/Nav.jsx:310-319`, `src/components/Footer.jsx:45-54`, `src/components/SplashScreen.jsx:51-82` — the brand mark is pasted three times with three different gradient ids.**
  `navBrand`, `footerBrand`, `launchBrand` are the same 2-path cloud SVG. Extract `<BrandMark size={26} />` (with `useId()` for the gradient id). The `Nav` copy also lacks `aria-hidden="true"` on the decorative `<svg>` (`:310`), unlike SplashScreen (`:57`).

- **[P3] `tailwind.config.mjs:95` and `:40-54` — two parallel color vocabularies, and a `darkMode` switch that nothing toggles.**
  `primaryTextColor/secondaryTextColor/bgColor/bgColorSecondary/primaryBtn/btnColor/otherColor` are legacy aliases of `bone/ink/carbon/projector`. Nothing in the codebase uses the legacy names (only `primaryBtn`, in dead `BottomNav.jsx:40`). `darkMode: "class"` is also inert — there is no `dark` class toggle anywhere (`MobileProfile.jsx` keeps an `isDarkMode` in local state and never applies it), and `color-scheme: dark` is hard-coded in `index.html:65`. Deleting the aliases and `darkMode` prevents the next dev from using the wrong token.

- **[P3] `tailwind.config.mjs:7-10` — `content` scans the entire NextUI theme dist, and `index.html` is not scanned.**
  `./node_modules/@nextui-org/theme/dist/**/*.{js,ts,jsx,tsx}` forces Tailwind to parse the whole library on every rebuild (the dominant cost in `npm run dev` on Windows), while `./index.html` is missing — irrelevant today, but any utility added to `index.html` (a `<noscript>` splash, a loading skeleton in `#root`) silently won't be generated. Narrow the glob to the theme files actually imported, or drop it if NextUI components are no longer used in the shell.

- **[P3] `src/main.jsx:20, 36-43, 49` — three long-lived listeners/timers with no teardown path.**
  `startContentRefresh()` is invoked after render and its returned unsubscribe is discarded; the `setInterval(checkForUpdate, 3600000)` and the `visibilitychange` listener inside `onRegisteredSW` are never cleared; the `pwa:apply-update` listener is added inside a promise and never removed. Under Vite HMR this leaks one of each per module reload, and in production the hourly `registration.update()` re-runs even when the tab has been backgrounded for days. Return the unsubscriber from `startContentRefresh`, keep the interval handle, and gate `registration.update()` on `document.visibilityState === "visible"`.
