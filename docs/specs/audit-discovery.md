# UI/UX Audit — Home / Discovery (CloudXMovies frontend)

Scope: `frontend-main/src/pages/{Home,Movies,Series,Anime,KDrama,TopRatedMovies,TopRatedSeries,MostViewed,GenrePage,MyList}.jsx`, `frontend-main/src/components/{HomeHero,HomeSections,SmartSection,TopTenToday,ContinueWatching,GenreSection,MovieCard,MovieCardSkeleton,MoviesAndSeriesSections,Pagination,EmptyState,Similars,WatchlistButton}.jsx`, `frontend-main/src/config/sectionsConfig.js`, `frontend-main/tailwind.config.mjs`, `frontend-main/src/index.css`. Backend contract checked in `filmsclub-backend/Backend/{fastapi/main.py,helper/database.py}` where the frontend depends on a response shape.

Severity legend: **P0** user sees nothing / cannot recover · **P1** major broken behaviour · **P2** real defect, workaround exists · **P3** polish / dead code / drift.

---

## Summary — the five structural problems behind most findings

1. **There is no error state anywhere in the discovery path.** `sectionsConfig.js` wraps every fetch in `safe() → []`, `SmartSection` treats `[]` as "hide the section", and every browse page swallows `.catch()` into `isLoading = false`. A backend outage renders a *blank* homepage behind a "late-night screening room" splash, and every list page says *"This shelf is still being stocked"* — i.e. a server failure is indistinguishable from an empty catalogue, with no retry.
2. **The homepage config is not deduplicated.** `popular-movies` and `top-rated-movies` issue the *identical* request (`rating:desc`, page 1, size 20); `apiCache` even returns the same cached promise, so the same 20 posters render twice under two different headings. Same for `popular-tv` / `top-rated-tv`.
3. **Card, skeleton and section-header markup has forked four ways.** `MovieCard` is the only correct implementation; `TopTenToday`, `ContinueWatching` and the grid variants each re-implement the poster (different radius, aspect strategy, `LazyLoadImage` wiring, colour tokens) and the skeletons reserve less height than the cards they replace, so every section load shifts the page.
4. **Filters and page numbers live only in component state.** `Movies.jsx` reads `?genre=` from the URL but `currentPage`, `sort_by` and every other filter are `useState` — so Back, refresh and "open in new tab" all reset to page 1, unhfiltered, and the "See all" links from language/rating sections drop the filter they promise.
5. **Two parallel watch-history stores.** `trackVisit()` writes `fc_watch_history`; `usePersonalizedRecommendations` and `WatchHistoryContext` read `watchHistory`. The "Picked for you" badge is therefore permanently dead, and the one component that would consume the real history (`ContinueWatching`) is never imported *and* would throw if it were.

---

## P0

### 1. A backend failure turns the homepage into a blank page — no hero, no sections, no message
- **File:** `frontend-main/src/pages/Home.jsx:59-70`, `frontend-main/src/config/sectionsConfig.js:17`, `frontend-main/src/components/SmartSection.jsx:39-43,83`, `frontend-main/src/components/HomeHero.jsx:26-31`
- **What's wrong:** `getJSON(BASE, "/api/hero")` only falls back to `/api/movies` on a **404**, but `main.py:3094` raises **400** on any hero failure — so a 400/500/offline falls straight into `.catch(() => setHeroLoading(false))`, leaving `heroMovies = []`. `HeroSlider` then hits `if (!movieData || movieData.length === 0) return null` and the hero is *removed*, not replaced by the skeleton. Meanwhile every section's `safe(promise).catch(() => [])` converts its error into an empty array, and `SmartSection:83` (`if (done && data.length === 0) return null`) unmounts the section. Net result: splash closes onto an essentially empty page with zero explanation and no retry.
- **Fix:** distinguish "empty" from "failed" and render something in both cases. Give `safe()` a sentinel instead of `[]`, carry it through `SmartSection`, and keep the hero box reserved:
  ```js
  // sectionsConfig.js
  const safe = (p) => p.then((d) => ({ ok: true, data: d })).catch(() => ({ ok: false, data: [] }));
  // SmartSection.jsx
  if (failed) return <SectionError title={config.title} onRetry={refetch} />;   // keeps ~1 row of height
  if (done && data.length === 0) return null;                                    // genuinely empty
  ```
  In `Home.jsx`, on hero failure keep `heroMovies` non-empty by falling back to the same `/api/movies` request for **any** error (not just 404), and if that also fails render a `HeroSkeleton`-height panel with "The projector is dark — retry".

### 2. Every browse page reports a network failure as an empty catalogue, with no retry
- **File:** `frontend-main/src/pages/Movies.jsx:46`, `Series.jsx:33`, `Anime.jsx:27`, `KDrama.jsx:27`, `TopRatedMovies.jsx:27`, `TopRatedSeries.jsx:27`, `MostViewed.jsx:26`, `SimilarMov.jsx:31`, `SimilarSeries.jsx:31`, `GenrePage.jsx:71-76`, `frontend-main/src/components/MoviesAndSeriesSections.jsx:79-109`
- **What's wrong:** All nine pages do `.catch(() => setIsLoading(false))` — the error object is discarded, `Items` stays `[]`, and `MoviesAndSeriesSections` therefore renders its "nothing here" branch: eyebrow **"House lights"**, title **"This shelf is still being stocked"**, description *"Nothing is screening here yet — check back after the next reel is loaded."* That is a lie on a 500/offline, and it offers no action. Worse, the component already has a full retry path (`props.error` → "Projection interrupted" + `<RetryButton onClick={props.onRetry}>`) that **no page in the app passes**, so the good code is unreachable. `GenrePage.jsx:71-76` does the same and additionally prints *"No {genre} movies yet"*.
- **Fix:** store the error, pass it down, and give the page a reload trigger:
  ```js
  const [error, setError] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);
  useEffect(() => { /* …fetch… */ .catch((e) => { setError(e); setIsLoading(false); }); },
            [movieFilter, currentPage, reloadToken]);
  <MoviesAndSeriesSections error={error} onRetry={() => setReloadToken((t) => t + 1)} … />
  ```
  In `GenrePage.jsx`, replace the ad-hoc empty block (`:164-178`) with the shared `<EmptyState>/<RetryButton>` so the three empty/error copies converge.

---

## P1

### 3. The homepage renders the same carousel twice under two different titles
- **File:** `frontend-main/src/config/sectionsConfig.js:150-156` vs `:166-172`, and `:159-165` vs `:173-179`
- **What's wrong:** `popular-movies` fetches `{sort_by:"rating:desc"}` and `top-rated-movies` fetches `{sort_by:"rating:desc"}` — byte-identical params, so `apiCache.keyFor` collapses them onto one cached promise (`utils/apiCache.js:4-9`) and the *same 20 posters* render under "Popular Movies" and again under "Top Rated Movies". `popular-tv` / `top-rated-tv` are the same pair for TV. `award-winning` (`min_rating: 8`, `:321-326`) is then a subset of that same ordering, and `hidden-gems` (`:327-333`) is only page 3 of it. Out of 24 sections, at least 6 are duplicates or subsets.
- **Fix:** give each section a distinct key so the cache and the UI both differ — e.g. `popular-movies` → `{sort_by:"view_count:desc"}` or `{sort_by:"updated_on:desc", page:2}`; `top-rated-movies` → `{sort_by:"rating:desc", min_rating:7.5}`; `award-winning` → `{min_rating:8, sort_by:"rating:desc", page:2}`. Add a startup assert so this cannot regress:
  ```js
  const seen = new Set();
  SECTIONS.forEach(s => {
    const key = s.id; if (seen.has(key)) throw new Error(`duplicate section ${key}`);
  });
  // plus a dev-only check on JSON.stringify(params) collisions inside fetchMovies/fetchTV
  ```

### 4. Genre filtering is applied twice with two different rules — valid titles vanish and pagination lies
- **File:** `frontend-main/src/pages/Movies.jsx:33-44` vs `frontend-main/src/pages/GenrePage.jsx:59-70`
- **What's wrong:** the backend already filters by `genre` (`database.py:1848-1858`, `{"genres": {"$in": […]}}`). `Movies.jsx` then re-filters client-side using **only the first genre** (`genresArr[0]`), so a film whose "Action" tag is second is silently dropped from an Action listing. `GenrePage.jsx` uses the correct `.some()` test but keeps the *unfiltered* `data.total_count`, so `Pagination` advertises pages whose grids are partially or wholly empty. `Movies.jsx:43` has the mirror problem: `data.total_count || moviesData.length` counts titles the client just removed.
- **Fix:** delete the client-side re-filter in `Movies.jsx` (trust the backend `$in` match) and in `GenrePage.jsx`; if you must keep a safety net, derive the total from the filtered result:
  ```js
  setItems(results);
  setTotalCount(results.length ? data.total_count : 0);   // and prefer server-side totals only
  ```
  If the server cannot be trusted, paginate client-side consistently instead of mixing the two counts.

### 5. "Picked for you" personalization can never activate — the hook reads a storage key nothing writes
- **File:** `frontend-main/src/hooks/usePersonalizedRecommendations.js:12,67-72`, `frontend-main/src/utils/watchHistory.js:7`, `frontend-main/src/context/WatchHistoryContext.jsx:19,31`
- **What's wrong:** `trackVisit()` — the only writer, called from `MovieDetails.jsx:7` / `TvDetails.jsx:7` — persists to `localStorage["fc_watch_history"]`. The personalization hook reads `localStorage["watchHistory"]`, and `WatchHistoryContext` reads/writes a *third* copy under `watchHistory`. The hook's `if (history.length < 2) return { items: similarList, isPersonalized: false }` therefore always fires, `isPersonalized` is never true, and the badge in `Similars.jsx:41-61` plus the score-based re-ranking are dead weight shipped to every client.
- **Fix:** one key, one module. Export the accessor from `utils/watchHistory.js` and import it in both consumers:
  ```js
  // usePersonalizedRecommendations.js
  import { getWatchHistory } from "../utils/watchHistory";   // reads "fc_watch_history"
  const history = getWatchHistory();
  ```
  Fold `WatchHistoryContext` onto the same key (or delete it — see #21) and add a unit test that `trackVisit()` twice makes `isPersonalized === true`.

### 6. Filters and page number never reach the URL — Back, refresh and sharing all reset to page 1
- **File:** `frontend-main/src/pages/Movies.jsx:11-19,69-77` (and the same pattern in every list page), `frontend-main/src/components/Pagination.jsx:40-48`
- **What's wrong:** `Movies.jsx` reads `?genre=` via `useSearchParams()` but `currentPage`, `movieFilter` and `movieFilterVal` are plain `useState`. Consequences: pressing Back after paging returns to the page-1 render; refreshing drops the reader back to page 1 of "Latest"; a link copied from page 7 opens page 1; and "Top Rated" (a filter, not just a page) is unreachable by URL. `Pagination` receives `onPageChange` only and never touches the router, so nothing can fix this from inside it.
- **Fix:** make the URL the state. Keep `?page=` and `?sort=` in `useSearchParams` and derive props from it:
  ```js
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Number(params.get("page")) || 1);
  const sort = params.get("sort") || "updated_on";
  const setPage = (p) => setParams((prev) => { const n = new URLSearchParams(prev); n.set("page", p); return n; });
  ```
  Have `Pagination` render `<Link to={{search}}>` buttons, or accept a `buildHref(page)` prop, so middle-click/ctrl-click on a page number works.

### 7. Collapsed lazy sections make every `IntersectionObserver` fire at once — 24 sections, ~30 concurrent requests
- **File:** `frontend-main/src/components/SmartSection.jsx:88-91`
- **What's wrong:** a `priority:"lazy"` section that has not been triggered renders `<div style={{minHeight: 1}} />`, and `SECTIONS` contains 17 lazy entries. All of them therefore sit within a ~17px band of each other on the page; the moment the last `priority:"high"` section is scrolled past, **every lazy sentinel is inside the `rootMargin: "400px 0px"` window simultaneously** and they all call `doFetch()` in the same frame. That defeats the lazy loading it was written for (the network burst is worse than eager loading) and, because the sentinels reserve no height, each replacement with a real carousel pushes everything below it down.
- **Fix:** reserve the real height while hidden, and drive the sections from one observer that only advances a cursor:
  ```jsx
  // keep layout stable + serialize the fetch order
  if (!done && !loading) {
    return (
      <div ref={sentinelRef} aria-hidden="true" className="mt-10 md:mt-14">
        <SectionHeaderSkeleton title={cleanTitle(config.title)} />
        <MovieCardSkeleton count={6} horizontal />
      </div>
    );
  }
  ```
  and gate `doFetch` behind a small module-level queue (or an `IO` with `threshold: 0` and a 1-section-at-a-time cursor) so at most 2–3 sections are in flight.

### 8. The hero description is *always* visible — inline `display` defeats `hidden mobile-desc:block`
- **File:** `frontend-main/src/components/HomeHero.jsx:268-283` (class on `:270`, inline `display` on `:278`)
- **What's wrong:** the element is declared `className="mobile-desc:block hidden"` (i.e. hide below 360px, show above) but the same element's `style` object sets `display: "-webkit-box"` for the `WebkitLineClamp` trick. Inline styles outrank any utility class, so `hidden` never applies: on a 320–359px phone the description still renders and eats the vertical budget the CTA needs. The `-webkit-box` and the responsive display were never compatible — one has to move to a wrapper.
- **Fix:** put the clamp on an inner element, or use the `line-clamp-2` utility (which already emits `-webkit-box`):
  ```jsx
  <p className="hidden mobile-desc:line-clamp-2" style={{ fontSize: "clamp(0.78rem,2.2vw,0.9rem)", … }}>
  ```
  and delete `display`/`WebkitLineClamp`/`WebkitBoxOrient`/`overflow` from the inline style.

### 9. Anime pagination throws away half of every page
- **File:** `filmsclub-backend/Backend/helper/database.py:2282-2291`, consumed by `frontend-main/src/pages/Anime.jsx:19-28`
- **What's wrong:** `get_anime()` pages the movie and TV clusters **separately** (`_paged_fanout(..., page, limit)` twice, so up to `2 × page_size` docs), merges, sorts, then returns `combined[:limit]` — 20 of ~40 are discarded per page — while `total_count = movie_total + tv_total` counts the whole library. So page 2 does not continue where page 1 stopped, roughly half the catalogue is unreachable through the UI, and the "N titles" count on the genre/list header disagrees with what pagination can produce. `sectionsConfig.js:70-77` (`fetchAnime`) has the same double source and re-sorts client-side.
- **Fix:** interleave deterministically after fetching the full merged window, or paginate the merge properly:
  ```python
  # fetch (page-1)*limit+limit from each cluster, merge+sort, then slice the GLOBAL window
  fetch = (page - 1) * limit + limit
  m_total, m_docs = await self._paged_fanout("movie", match, sort_list, 1, fetch)
  t_total, t_docs = await self._paged_fanout("tv", match, sort_list, 1, fetch)
  merged = sorted(m_docs + t_docs, key=…, reverse=True)
  skip = (page - 1) * limit
  return {"total_count": m_total + t_total, "results": merged[skip:skip + limit]}
  ```

### 10. Skeletons reserve less height than the cards they stand in for — every section load shifts the page
- **File:** `frontend-main/src/components/MovieCardSkeleton.jsx:8,13,15-18` vs `frontend-main/src/components/MovieCard.jsx:31,69-88`, `frontend-main/src/components/HomeSections.jsx:89`
- **What's wrong:** a real card renders poster + `mt-2` + a `line-clamp-2` title (`text-[13px] sm:text-sm`, `leading-snug` → up to ~35px) + up to **two** meta lines (`episode_label` + `episode_title`, or `release_year`). The skeleton renders one `h-3` bar and one `h-2.5` bar (~28px total). Measured delta ≈ **22px per card**: on a 7-column grid that is 22px per row of shift, and on the horizontal rails the whole row grows by 22px when data lands. The horizontal skeleton also uses `gap-3 sm:gap-4` while the real scroller uses `gap-2.5 sm:gap-3` (`HomeSections.jsx:89`), so card *x*-positions move as well, and the skeleton breaks at `min(count,10)` while the real section may hold 20.
- **Fix:** mirror the real card exactly — same gap, `mt-2`, two title lines, one meta line, and the same `clamp()` width:
  ```jsx
  <div className="flex gap-2.5 sm:gap-3 overflow-hidden pb-3">   {/* same as HomeSections */}
    <div className="aspect-[2/3] rounded-[4px] bg-carbon skeleton" />
    <div className="mt-2 space-y-1.5 px-0.5">
      <div className="h-2.5 w-full rounded-[2px] bg-bone/10 skeleton" />
      <div className="h-2.5 w-3/4 rounded-[2px] bg-bone/10 skeleton" />   {/* 2nd title line */}
      <div className="h-2.5 w-1/3 rounded-[2px] bg-bone/[0.06] skeleton" />
    </div>
  ```

---

## P2

### 11. "Top 10 Today" has no empty or error state, and ranks 4-10 are effectively invisible
- **File:** `frontend-main/src/components/TopTenToday.jsx:13-25,56`
- **What's wrong:** `if (!data || data.length === 0) return null;` — and `get_trending_today` filters on `{"date": today}`, so on any day with no tracked views (every fresh deployment, every quiet night, any 500) the entire shelf silently disappears with no message. Separately, `RankNumber` paints non-top-3 ranks with `text-bone/20` **and** `WebkitTextFillColor: "rgba(242,237,227,0.04)"` over a `0.10` stroke — roughly a 1.1:1 contrast ratio against `#08090B`. Ranks 4-10, i.e. 70% of the ranking signal, are unreadable; only 01-03 in projector gold are legible.
- **Fix:** render an explanatory empty state after loading (`<EmptyState eyebrow="No screenings yet" title="Today's chart is empty" />`) rather than `null`, and raise the non-top-3 treatment to at least `text-bone/40` with `WebkitTextFillColor: "rgba(242,237,227,0.10)"` + stroke `0.22`.

### 12. `TopTenToday` posters crop instead of cover — `LazyLoadImage` is missing `wrapperClassName`
- **File:** `frontend-main/src/components/TopTenToday.jsx:99-104` vs `frontend-main/src/components/MovieCard.jsx:32-38`
- **What's wrong:** `MovieCard` passes `wrapperClassName="absolute inset-0 w-full h-full"`, which is what makes its `<img className="w-full h-full object-cover">` resolve. `TopTenToday` omits it, so the library's wrapper `<span>` falls back to its own inline style `display: inline-block; height: auto` (`node_modules/react-lazy-load-image-component/src/components/LazyLoadImage.jsx:117-123`). `h-full` on the img then resolves against an auto-height parent (→ `auto`), so `object-cover` never engages and any poster that is not exactly 2:3 is simply clipped by the container's `overflow-hidden`. The same omission appears in `ContinueWatching.jsx:46-51` and `Nav.jsx:37-42`.
- **Fix:** always pass the wrapper class when a fixed-ratio box is involved, and give the img an explicit ratio fallback:
  ```jsx
  <LazyLoadImage
    src={item.poster || posterPlaceholder}
    alt={item.title}
    effect="black-and-white"
    wrapperClassName="absolute inset-0 w-full h-full"
    className="w-full h-full object-cover object-center"
  />
  ```

### 13. Four forked poster-card implementations, differing on radius, aspect, tokens and effects
- **File:** `frontend-main/src/components/MovieCard.jsx:25,31,36-37`, `TopTenToday.jsx:98-104`, `ContinueWatching.jsx:45-51,58`, `frontend-main/src/components/MoviesAndSeriesSections.jsx:111-127`
- **What's wrong:** there is no shared card primitive, so the same poster renders four ways: `MovieCard` (`rounded-[4px]`, `aspect-[2/3]`, `ring-1 ring-bone/10`, `effect="blur"`, wrapper class present, `WatchlistButton` md, title `text-[13px] sm:text-sm` + `line-clamp-2`); `TopTenToday` (`rounded-[4px]`, `aspect-[2/3]`, **no ring until hover**, `effect="black-and-white"`, no wrapper class, `WatchlistButton` sm, title `text-[11px] sm:text-xs`); `ContinueWatching` (**`rounded-xl`**, **inline `style={{aspectRatio:'16/9'}}`**, `bg-[#111]`, literal `text-white`/`text-white/90`/`text-white/40` instead of the `bone` token, 44px circular overlay, red `from-red-500 to-red-700` progress bar); and the grid wrappers in `MoviesAndSeriesSections`/`Similars`/`MyList`/`GenrePage` which each re-declare the identical 7-column grid string by hand (four copies). Nothing enforces `aspect-[2/3]`: `rounded-xl` appears 17× in 6 files and `text-white` 16× in 9 files.
- **Fix:** extract `PosterCard` with the canonical geometry and pass only the deltas:
  ```jsx
  // components/PosterCard.jsx  — one implementation, used by all four call sites
  export function PosterCard({ item, size = "md", ratio = "2/3", badge, footer }) { … }
  export const MEDIA_GRID = "grid gap-x-2 sm:gap-x-3 gap-y-6 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7";
  ```
  Then delete `ContinueWatching`'s bespoke poster (see #21) and the duplicated grid strings.

### 14. Hover-only affordances are unreachable by touch and keyboard
- **File:** `frontend-main/src/components/MovieCard.jsx:58-62`, `TopTenToday.jsx:107-111`, `ContinueWatching.jsx:57-61`
- **What's wrong:** the play badge on every card is `opacity-0 group-hover:opacity-100`. On a phone there is no hover, so the strongest "this is playable" cue never appears; on a keyboard, focus lands on the stretched `<Link>` (which `MovieCard` correctly styles with `focus-visible:ring`), but `group-hover` is not triggered by focus, so the overlay stays invisible. `ContinueWatching` is worse: its play glyph and its remove button are both hover/tap-only overlays with no focus styles at all.
- **Fix:** add the focus variants everywhere the hover variants exist (`group-hover:opacity-100 group-focus-within:opacity-100`), and on touch use a always-visible condensed affordance:
  ```jsx
  <div className="… opacity-0 transition-opacity duration-300
                  group-hover:opacity-100 group-focus-within:opacity-100
                  max-sm:opacity-100 max-sm:bg-ink/30">
  ```

### 15. Horizontal rails hijack the page swipe and clip the focus ring on the edge cards
- **File:** `frontend-main/src/components/HomeSections.jsx:87-95`, `TopTenToday.jsx:71`, `ContinueWatching.jsx:30`
- **What's wrong:** none of the three scrollers sets `overscroll-behavior-x: contain`, so a horizontal swipe that reaches the end of a rail is chained to the browser — on iOS Safari and in the installed PWA that is a **back-navigation gesture**, i.e. swiping the last card kicks the reader off the page. All three also render cards flush against the container edge inside an `overflow-x-auto` box, so `MovieCard`'s `focus-visible:ring-2 ring-offset-2` is clipped on the first and last card (the scroller has no padding and no `scroll-padding-inline`). `body { overflow-x: hidden }` (`index.css:34`) hides the symptom of the leak but not the gesture theft.
- **Fix:** `className="… overflow-x-auto overscroll-x-contain snap-x snap-mandatory pb-3 -mx-1 px-1 scroll-px-1 no-scrollbar"` so the ring offset has room, and audit for any remaining `overflow-visible` (see #17).

### 16. The drag-scroll guard in `HomeSections` never fires, so a drag-release navigates to the card
- **File:** `frontend-main/src/components/HomeSections.jsx:16-45,90,104-106`
- **What's wrong:** `isDragging.current` is set `true` on `mousedown` and cleared in `stopDrag` on `mouseup`/`mouseleave`. The browser dispatches `click` **after** `mouseup`, so by the time the item's `onClick` runs `isDragging.current` is already `false` and `e.preventDefault()` never executes. A short drag that begins and ends on the same card therefore triggers navigation (the accidental-click case the guard was written for), while a drag released over a different card fires no item click at all (so the guard protects nothing). Compounding it, `style={{ scrollSnapType: "x mandatory" }}` combined with the JS `scrollLeft` assignment in `onMouseMove` fights the snap engine, producing the rubber-band jerk that a manual drag is meant to avoid.
- **Fix:** record the drag distance and cancel the click explicitly:
  ```js
  const moved = useRef(0);
  const onMouseDown = (e) => { dragging.current = true; moved.current = 0; … };
  const onMouseMove = (e) => { if (!dragging.current) return; moved.current += Math.abs(e.movementX); … };
  const onClickCapture = (e) => { if (moved.current > 6) { e.preventDefault(); e.stopPropagation(); } };
  ```
  and use `scrollSnapType: "x proximity"` (or drop it) while a mouse drag is active.

### 17. `!overflow-visible` on a Swiper can leak page-level horizontal scroll
- **File:** `frontend-main/src/components/GenreSection.jsx:101`, `frontend-main/src/index.css:34,944-950`
- **What's wrong:** the slide is `!overflow-visible`, which directly contradicts `.genre-swiper { overflow: hidden !important }` two layers below it (the `!` utility wins on specificity/source order). Off-screen slides are therefore laid out outside the container, and the resulting overflow is only suppressed globally by `body { overflow-x: hidden }` — a page-wide band-aid that also hides genuine overflow bugs and breaks `position: sticky` containment. On iOS the overhanging slides widen the scrollable area, letting the page nudge sideways.
- **Fix:** remove `!overflow-visible` from the Swiper className (the `genre-swiper` rule already does the right thing) and, if the glow/shadow needs headroom, reserve it with `.genre-swiper { padding: 4px 0 8px }` instead of `overflow: visible`.

### 18. Body text repeatedly sits below WCAG AA — 214 instances of `text-bone/20`–`/40` across 43 files
- **File:** `frontend-main/src/index.css:5-15` (tokens), examples in scope: `HomeHero.jsx:68,86,241-262`, `TopTenToday.jsx:19,130`, `HomeSections.jsx:74`, `GenreSection.jsx:81`, `Pagination.jsx:114`, `Similars.jsx:68`, `SearchResults.jsx:76`
- **What's wrong:** on `--ink: #08090B`, `bone` at 40% opacity composites to ≈ `#6B6862` (≈3.4:1), at 35% ≈ 2.9:1, at 30% ≈ 2.4:1, at 20% ≈ 1.7:1 — all under the 4.5:1 required for the ~10–12px text they are used on. Counted in the current tree: `text-bone/20` 11×, `/25` 10×, `/30` 18×, `/35` 54×, `/40` 92×, `/45` 29× = **214 instances over 43 files**, with **49 distinct `bone` opacity steps** in use — evidence that no rule was ever set. In this scope alone the hero programme line (`/40` for genres, `/20` for the separators), the section "See all" (`/70` is fine, but `GenreSection`'s is `/45`), the pagination caption (`/35`) and the Top 10 rank numbers are the visible casualties.
- **Fix:** define a three-step text scale in the theme and forbid raw opacities:
  ```js
  // tailwind.config.mjs → extend.textColor
  text: { muted: "rgba(242,237,227,0.72)", faint: "rgba(242,237,227,0.60)", disabled: "rgba(242,237,227,0.45)" }
  ```
  then mechanically replace `text-bone/[2-4]5` → `text-muted` (0.72 → 8.6:1) and `text-bone/[2-3]0` → `text-faint`; keep sub-40% only for genuinely decorative separator glyphs. Add an eslint/tailwind rule or a CI grep to stop new `/20`–`/40` text from landing.

### 19. Section headers have forked into three inconsistent variants
- **File:** `frontend-main/src/components/HomeSections.jsx:50-81`, `TopTenToday.jsx:61-68`, `GenreSection.jsx:70-88`, `Similars.jsx:29-75`
- **What's wrong:** `HomeSection` uses `items-end … border-b border-bone/10 pb-3`, title `text-[1.3rem] sm:text-2xl md:text-[1.7rem] leading-none truncate`, and a "See all" link styled `timecode text-[10px] uppercase tracking-[0.16em] text-bone/70`. `TopTenToday` uses a bare `justify-between` row with **no rule and no "See all" at all** (despite being a ranked shelf that deserves a link to `/most-viewed`), title `text-base sm:text-lg md:text-xl`. `GenreSection`/`Similars` drop the rule, keep `text-base sm:text-lg md:text-xl`, and style "See all" as `font-mono text-[11px] uppercase tracking-wider text-bone/45` — two different right-hand link treatments, three title sizes, two accent-bar widths (`w-px h-5` vs `w-px h-5`, but `MyList.jsx:60` uses `w-px h-7`). Heading level also drifts: `h1` in `MoviesAndSeriesSections.jsx:28` and `GenrePage.jsx:103`, `h2` in the four carousels, `h3` in `MovieCard.jsx:70`, `h2` again in `EmptyState.jsx:28` — so the outline order depends on which component happens to render.
- **Fix:** one `<SectionHeader title accent="bar|rule" action?>`, exported beside `PosterCard`, with a `level` prop (`as="h2"`, `as="h3"`) so the document outline is explicit; then delete the four inline header blocks. Give `TopTenToday` the same "See all → `/most-viewed`" action.

### 20. Loading placeholders drifted from their components (widths, counts, ranks)
- **File:** `frontend-main/src/components/TopTenToday.jsx:42-51` vs `:80-86`, `GenreSection.jsx:106` vs `MovieCardSkeleton.jsx:13`, `Similars.jsx:97`, `MoviesAndSeriesSections.jsx:130`
- **What's wrong:** the Top-10 skeleton uses `w-[100px] sm:w-[130px]` and no `md:` step while the real item is `w-[100px] sm:w-[128px] md:w-[140px]`; it also omits the `paddingLeft: 18px / paddingBottom: 16px` the real item applies for the rank number, so every card shifts 2px at `sm`, 12px at `md` and the first card jumps 18px when data lands. Rail widths are inconsistent app-wide: `HomeSections.jsx:103` and `MovieCardSkeleton.jsx:13` use `clamp(118px, 18vw, 190px)` while `GenreSection.jsx:106` uses `clamp(110px, 18vw, 180px)`. `Similars` shows 10 skeleton cells but can render 40 items; `MoviesAndSeriesSections` hardcodes 20 while `GenrePage` renders `PAGE_SIZE` (24).
- **Fix:** export the geometry constants once and import them in both the card and the skeleton:
  ```js
  export const RAIL_CARD_W = "clamp(118px, 18vw, 190px)";
  export const TOP10_CARD_W = "100px";   // + sm/md steps via the same map
  ```
  and derive the skeleton count from the page size (`count={PAGE_SIZE}`) instead of literals.

### 21. `ContinueWatching` and `GenreSection` are dead code — and would crash or mislabel content if wired
- **File:** `frontend-main/src/components/ContinueWatching.jsx:6,10,17`, `frontend-main/src/components/GenreSection.jsx:41-62`, `frontend-main/src/pages/Home.jsx:123-130`
- **What's wrong:** both components are imported **nowhere** (`Home.jsx` renders only `HeroSlider`, `TopTenToday` and `SmartSection`; `sectionsConfig.js` has no entry for either). That matters because they are broken in ways that will only surface when someone wires them up: (a) `ContinueWatching` calls `useWatchHistory()`, whose provider `WatchHistoryProvider` is never mounted (`main.jsx:10-18` / `App.jsx`), so it would throw *"useWatchHistory must be used within a WatchHistoryProvider"* on first render; (b) it reads the `watchHistory` key while `trackVisit` writes `fc_watch_history`, so even with a provider it would always render `null`; (c) its remove `<button>` (`:74-83`) is nested **inside** the `<Link>` (`:36-43`) — invalid interactive nesting, and its 28px hit area is below the 44px minimum; (d) `GenreSection` sends `genre_id` (`:45`) but `/api/movies` only accepts `genre` by **name** (`main.py:3072`), so FastAPI silently ignores it and the shelf shows a generic top-rated list under a "Horror" heading; its error path (`:52-62`) re-issues the request **without** any genre filter and displays the result as that genre.
- **Fix:** either delete both files, or fix then wire them: delete the `genre_id` branch in favour of `{ genre: genre.label }` and *never* fall back to an unfiltered query (show an error row instead); move the remove button out of the `<Link>` into a sibling overlay with `aria-label="Remove from continue watching"` and `min-h-[44px] min-w-[44px]`; switch it to `getWatchHistory()` from `utils/watchHistory`. Add `continue-watching` to `sectionsConfig.js` only after (b) is resolved.

### 22. `SmartSection`'s own error handling is unreachable, and its "dedup guard" does not exist
- **File:** `frontend-main/src/components/SmartSection.jsx:8-10,27-44`
- **What's wrong:** the header comment promises "Auto-hides (returns null) when fetch resolves to 0 items" and "`id` … used as React key + **dedup guard**". There is no dedup anywhere — ids being unique in the array is the only thing preventing duplicates, and two sections with identical *content* still render (see #3). Likewise the `.catch()` at `:39-43` can never run, because `sectionsConfig.safe()` already converts every rejection into an empty array; the error branch only flips `loading/done`, which lands on the same `return null` as an empty result. So the component has no way to distinguish "nothing matched the filter" from "the server is down".
- **Fix:** remove `safe()` from the config helpers (let rejections propagate), then give `SmartSection` a real third state — `error` → a compact inline row with the section title and a Retry — and drop the inaccurate comment. Add the duplicate-id assertion from #3.

### 23. Touch targets below 44px across the carousels and pagination
- **File:** `frontend-main/src/components/HomeSections.jsx:56-69` (`w-8 h-8` = 32px), `Pagination.jsx:28,76` (`w-9 h-9` = 36px), `WatchlistButton.jsx:71` (`w-7 h-7` = 28px / `w-8 h-8` = 32px), `HomeHero.jsx:108-117` (mobile rail dots `h-[3px] w-3`, 3px tall)
- **What's wrong:** WCAG 2.2 §2.5.8 asks for ≥24×24 CSS px and platform guidance for 44×44; the carousel chevrons, the pagination buttons, the heart toggle and the hero's mobile reel indicators are all under that. The hero dots are the worst: a 3px-tall sliver with no padding, sitting at `bottom-4` where it competes with the browser's gesture area. Since the chevrons are `hidden sm:flex`, sub-480px users have neither a sized control nor a visible affordance.
- **Fix:** keep the visual size and expand the hit area:
  ```jsx
  <button className="relative flex items-center justify-center w-8 h-8 before:absolute before:-inset-1.5 before:content-['']" … />
  ```
  For the hero dots, wrap the 3px bar in a `py-3` button so the target is 44px tall while the bar stays thin.

### 24. `Pagination` is not a landmark and does not expose the current page
- **File:** `frontend-main/src/components/Pagination.jsx:49-117`
- **What's wrong:** the container is a plain `<div>` (not `<nav aria-label="Pagination">`), the active page button has **no** `aria-current="page"` — its state is conveyed only by `motion.div` colour — and the "…" separators are `<span>` elements with no `aria-hidden`, so a screen reader hears "ellipsis" as content. The disabled first/previous buttons are correctly `disabled`, which is good, but there is no live announcement that the page changed, and `Page {currentPage} of {pagesCount}` at `:113-117` is inside the same container rather than being an `aria-live` region.
- **Fix:**
  ```jsx
  <nav aria-label="Pagination">
    …
    <button aria-current={isActive ? "page" : undefined} …>{page}</button>
    <span aria-hidden="true">…</span>
    <p role="status" className="sr-only">Page {currentPage} of {pagesCount}</p>
  </nav>
  ```

### 25. `fontSize` and `screens` were overridden at the theme root, which stripped line-heights and produced 285 arbitrary sizes
- **File:** `frontend-main/tailwind.config.mjs:11-25,59-74`; usage examples `HomeSections.jsx:51`, `MovieCard.jsx:43,70,74,79,84`, `TopTenToday.jsx:18,123,128,130`, `HomeHero.jsx:83,120,237,293`, `Similars.jsx:50-55`, `Pagination.jsx:114`
- **What's wrong:** `fontSize` is declared **outside** `extend`, so it replaces Tailwind's map entirely — and every entry is a bare string, which means `text-sm` no longer ships its paired `line-height`. That is why the codebase is littered with compensating `leading-*`/`leading-none` and with `text-[Npx]` arbitrary values (27 in this scope, 285 app-wide) that bypass the scale altogether: `text-sm` is silently 0.8rem (not 0.875), `text-lg` 1.1rem, `text-2xl` 1.4rem, so any developer sizing by memory gets the wrong result and hand-writes a pixel value instead. `screens` is likewise overridden at the root: `sm:480` / `md:700` / `lg:1024` / `xl:1440` / `2xl:1536` — every breakpoint moved, `md` is an unusual 700, and `xxl:1600` sits *between* `2xl:1536` and the `max-w-[1800px]` shell, so layouts jump at unexpected widths.
- **Fix:** move both under `extend` and give the sizes their line-heights so the scale is usable again:
  ```js
  extend: {
    fontSize: {
      xs: ["0.75rem", { lineHeight: "1.1" }],
      sm: ["0.875rem", { lineHeight: "1.35" }],
      base: ["1rem", { lineHeight: "1.6" }],
      lg: ["1.125rem", { lineHeight: "1.4" }],
      "2xl": ["1.5rem", { lineHeight: "1.25" }],
    },
    screens: { sm: "640px", md: "768px", lg: "1024px", xl: "1280px", "2xl": "1536px" },
  }
  ```
  Then sweep `text-[Npx]` → scale tokens. (Expect a visual diff; do it with the design tokens consolidated first.)

---

## P3

### 26. The kind chip is redundant on every single-kind grid
- **File:** `frontend-main/src/components/MovieCard.jsx:49-51`
- **What's wrong:** every card renders a "Movie"/"Series" chip in the top-left. On `/Movies`, `/top-rated-movies`, `/most-viewed` (movies only) and any genre grid it repeats the page title on all 20 cards, competing with the rating chip for the same tiny poster corner.
- **Fix:** make it opt-in via a prop (`showKind={false}` on homogeneous grids, `true` on mixed shelves like `trending`/`most-watched`/`recommended`).

### 27. Grid items are keyed by array index, so pagination reuses the wrong DOM
- **File:** `frontend-main/src/components/MoviesAndSeriesSections.jsx:119`
- **What's wrong:** `key={index}` means that when the page changes, React reuses each mounted card node and only swaps the `movie` prop — entrance animations never replay (so the `opacity/y` stagger at `:120-122` runs once, ever), and any per-card local state (image load state, `WatchlistButton`'s `saved`) is carried over to a different title. `Similars.jsx:87` and `MyList.jsx:147` already do this correctly.
- **Fix:** `key={`${movie.media_type ?? "m"}:${movie.tmdb_id ?? movie._id ?? index}`}`.

### 28. Hero reel buttons misuse `aria-current` and are the only hero navigation
- **File:** `frontend-main/src/components/HomeHero.jsx:74-80,108-117`
- **What's wrong:** slide pickers are `<button aria-current="true">`. `aria-current` is meant to mark an item *within a set of links/pages*, and it gives no "1 of 12" context here; a tab pattern (`role="tablist"` + `aria-selected`) or a simple `aria-label={`Slide ${i+1} of ${n}`} aria-pressed` is what a screen-reader user needs. Autoplay also pauses on hover (`pauseOnMouseEnter: true`, `:50`) but **not** on keyboard focus, so a keyboard user reading the hero gets the slide swapped underneath them every 7.5s; and `onSlideChange` uses `realIndex` while the rail dumps every slide's title into the accessible name (`:78`), so with a 12-slide loop each button is labelled but the group is unlabelled.
- **Fix:** wrap the rail in `role="group" aria-label="Choose a featured title"`, switch to `aria-pressed={isActive}`, and pause on focus:
  ```js
  onFocusCapture={() => swiperRef.current?.autoplay?.stop()}
  onBlurCapture={() => swiperRef.current?.autoplay?.start()}
  ```

### 29. Entrance animations ignore `prefers-reduced-motion` in five of six components
- **File:** `frontend-main/src/components/TopTenToday.jsx:75-81`, `GenreSection.jsx:108-112`, `Similars.jsx:79-95`, `MoviesAndSeriesSections.jsx:111-126`, `MyList.jsx:139-154`
- **What's wrong:** `HomeSections.jsx:14,99` is the only one that gates its `motion.div` on `useReducedMotion()`; the other five always animate (`initial={{opacity:0,y:10}}` + a staggered `delay`). `index.css:1052-1071` suppresses CSS animation under `prefers-reduced-motion`, but framer-motion animates via inline styles and is unaffected — so users who asked for reduced motion still get up to 20 staggered fade-ups per shelf.
- **Fix:** hoist a shared variant set and use it everywhere:
  ```js
  const reduce = useReducedMotion();
  const item = { initial: reduce ? false : { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 } };
  ```

### 30. Hero height has no fallback unit and is coupled to the shell's negative margin
- **File:** `frontend-main/src/components/HomeHero.jsx:13,19,39`, `frontend-main/src/App.jsx:61`
- **What's wrong:** `HERO_H = "clamp(480px, 88svh, 760px)"` uses `svh` only. In any browser without `svh` support the whole `clamp()` is an invalid declaration, the inline `height` is dropped, and the hero collapses to its content height — and because the wrapper carries `-mt-16 md:-mt-20` to tuck under the fixed nav, the collapsed hero slides *behind* the nav. The same magic `-mt-16/20` pair is also duplicated in `HeroSkeleton`, so any change to `App.jsx:61`'s `pt-[calc(var(--safe-top)+4rem)]` desynchronises them silently.
- **Fix:** declare both units in that order and share one constant:
  ```js
  export const HERO_H = "clamp(480px, 88vh, 760px)";
  export const HERO_H_SVH = "clamp(480px, 88svh, 760px)";   // applied second, overrides where supported
  ```
  or use `min-h-[min(88svh,760px)]` with a `min-h-[min(88vh,760px)]` fallback, and derive the negative margin from a CSS var defined alongside the shell padding.

---

## Recommended order of work

1. **Make failure visible (#1, #2, #22).** Remove `safe()` from `sectionsConfig.js`, thread `error`/`onRetry` into `MoviesAndSeriesSections` from every page, add an error row to `SmartSection`, and give `Home.jsx` a hero fallback for *any* error. This is the single highest-value change: it turns nine silent blank states into recoverable ones.
2. **Fix state that the user cannot recover without it (#6, #4).** Move `page` and `sort` into `useSearchParams`, and delete the divergent client-side genre re-filters so counts and contents agree.
3. **Stop shipping the same shelf twice (#3, #9).** De-duplicate `SECTIONS` (with the startup assert) and fix `get_anime` pagination.
4. **Restore the promised personalization (#5, #21).** Unify on `fc_watch_history`, then either delete `ContinueWatching`/`GenreSection` or fix and wire them.
5. **Stabilise layout (#7, #10, #20).** Reserve real height for hidden/lazy sections, correct the skeleton metrics, and unify rail geometry constants. Do this before #13 so the card refactor has one target shape.
6. **Consolidate the card and header primitives (#13, #19, #26, #27).** Introduce `PosterCard`, `SectionHeader` and `MEDIA_GRID`, migrate the four call sites, and delete the bespoke duplicates.
7. **Accessibility and touch pass (#8, #11, #14, #18, #23, #24, #28, #29).** Hero description `hidden` bug, rank contrast, focus-visible overlays, the `bone` text scale, 44px targets, pagination landmark, reduced-motion gating.
8. **Token cleanup (#25, #30).** Move `fontSize`/`screens` under `extend` and sweep `text-[Npx]` last — it is the largest-mechanical-diff item and will mask everything above if done first.
