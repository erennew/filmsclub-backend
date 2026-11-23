# UI/UX Audit — Detail, Search & Cast Surfaces

**Scope:** `frontend-main/src/pages/MovieDetails.jsx`, `TvDetails.jsx`, `src/components/MoviesAndSeriesDetailsSections.jsx`, `MobileMovieDetails.jsx`, `CastSection.jsx`, `QualityTab.jsx`, `UpcomingEpisodeCard.jsx`, `src/pages/CastMedia.jsx`, `SearchResults.jsx`, `MobileSearch.jsx`, `SimilarMov.jsx`, `SimilarSeries.jsx`, `NotFound.jsx`, `src/components/SEO.jsx` (+ `tailwind.config.mjs`, `src/index.css` for tokens, `App.jsx`/`Nav.jsx` for routing context).
**Stack:** React 18, Vite 5, Tailwind 3.4, framer-motion 11, react-helmet-async, plyr-react.
**Date:** 2026-11-08. No source files were modified.

### Headline answers to the brief

| Question | Answer |
|---|---|
| Is there a duplicated mobile + desktop detail tree? | **No.** `MobileMovieDetails.jsx` (247 lines) is **never imported** — `grep` for `MobileMovieDetails` returns only its own definition/export, and `/mov/:movieID` → `MovieDetails.jsx` only (`App.jsx:64`). There is no double DOM/double fetch from a duplicate tree. The double work is elsewhere: `TvDetails` fetches `/api/id/:id` **twice** per season (see F-06). |
| Is there a duplicated search UI? | **Yes, three times over**: `Nav.jsx` desktop dropdown, `Nav.jsx` full-screen mobile overlay (lines 510–667), and a **dead** `MobileSearch.jsx` page that is not routed. Copy, limits and empty states have already drifted. |
| Are tabs real tabs? | **No.** Season selector (`MoviesAndSeriesDetailsSections.jsx:580`) and quality selector (`QualityTab.jsx:31`) are bare `<div>`+`<button>`. Zero `role="tablist"`, `aria-selected`, `aria-controls`, or arrow-key handling. The only `role`/`aria-*` in the whole 862-line details component is one `aria-label` (`:82`). |

---

## P0 — Broken primary actions

### F-01 · P0 · `QualityTab.jsx:67-69,100-107` — "Watch online" CTA links to a route that does not exist
`onlineWatchUrl` is `/watch/series/...` or `/watch/movie/...`, rendered as the `<a href>` of the primary gold button on every file card. `App.jsx:62-80` has **no `/watch/*` route**, so the catch-all `*` → `NotFoundPage` fires. The most prominent action in the series download section dead-ends on the 404 page.

```jsx
// QualityTab.jsx:67
const onlineWatchUrl = seasonNumber
  ? `/watch/series/${tmdbId}/${db_index}/${seasonNumber}/${episodeNumber}/${selectedQuality}/${index}`
  : `/watch/movie/${tmdbId}/${db_index}/${selectedQuality}/${index}`;
```
**Fix:** point it at the in-page player instead of a phantom route — the player already exists above the episode list:
```jsx
<button
  type="button"
  onClick={() => document.getElementById("cx-player")?.scrollIntoView({ behavior: "smooth", block: "start" })}
  className="…"
>
```
(`<div ref={playerWrapperRef} …>` at `MoviesAndSeriesDetailsSections.jsx:333` should get `id="cx-player"`.) If `/watch` is real and simply missing from the router, add the route; do not ship a link that 404s.

### F-02 · P0 · `MoviesAndSeriesDetailsSections.jsx:129-144` — quality list intentionally drops 480p/720p/1080p for series, so most seasons render **no** download/watch section at all
The memo adds a quality only when it is *not* one of the three common ones, for `detailType === "series"`:
```jsx
// :135-137
if (props.detailType === "series") {
  if (q.quality !== "480p" && q.quality !== "720p" && q.quality !== "1080p") qualitySet.add(q.quality);
} else qualitySet.add(q.quality);
```
`QualityTab` early-returns `null` when `qualities.length === 0` (`QualityTab.jsx:26`). A season whose files are all 1080p therefore loses the entire quality/files block — no download, no Telegram, no subtitles — while the episode rows still show per-episode quality pills. The inversion looks like a leftover debugging filter.
**Fix:** drop the inversion; series should surface every quality, ordered:
```jsx
props.episodes?.forEach((ep) => {
  const fullEps = season?.episodes?.find((e) => e.episode_number === ep.episode_number);
  fullEps?.sources?.forEach((q) => q.quality && qualitySet.add(q.quality));
});
```

---

## P1 — Broken or inaccessible, high-traffic paths

### F-03 · P1 · `SearchResults.jsx:24-48` — two effects fight over `currentPage`, causing a stale-page fetch and a double request per new query
```jsx
// :24-28  effect A
useEffect(() => { setIsLoading(true); setCurrentPage(1); window.scrollTo(0, 0); }, [searchResult]);
// :30-48  effect B
useEffect(() => { … axios.get(… params: { query: searchResult, page: currentPage … }) … },
  [searchResult, currentPage, BASE, reloadToken]);
```
When the user searches a second term *from page 2*, both effects run on the same commit: B fires with the **new** query and the **old** `currentPage` (page 2), then A's `setCurrentPage(1)` triggers B again — two requests, and a window where page-2 results of the new query are painted. No `AbortController`, so whichever response lands last wins.
**Fix:** derive the page from the URL and reset it inside the navigation, not in a second effect:
```jsx
const [params, setParams] = useSearchParams();
const currentPage = Number(params.get("page") || 1);
useEffect(() => {
  const ac = new AbortController();
  setIsLoading(true); setError(false);
  axios.get(`${BASE}/api/search/`, {
    params: { query: searchResult, page: currentPage, page_size: 20 },
    signal: ac.signal,
  })
    .then(({ data }) => { setMovies(data.results || []); setTotalCount(data.total_count || 0); })
    .catch((e) => { if (!axios.isCancel(e)) setError(true); })
    .finally(() => setIsLoading(false));
  return () => ac.abort();
}, [searchResult, currentPage, BASE, reloadToken]);
```
Also clears F-19 (page state lost on back).

### F-04 · P1 · `MoviesAndSeriesDetailsSections.jsx:203-211` — playing an episode does not put anything in the URL: no deep link, no back/forward, refresh loses the episode
```jsx
const handlePlayEpisode = useCallback((eps) => {
  …
  props.setEpisodeNumber(eps.episode_number);
  setTimeout(() => { playerWrapperRef.current?.scrollIntoView({…}); }, 50);
}, [props]);
```
`?season=&episode=` is never written. A user who refreshes, taps a shared link, or presses Back after scrolling 40 episodes lands back on “Pick an episode to start the screening”. The `setTimeout(…, 50)` is also a race against React's commit — on a slow mobile device the ref can still be `null`.
**Fix:** own the selection in the URL and drop the timeout (scroll in a layout effect keyed to the active episode):
```jsx
const [sp, setSp] = useSearchParams();
const play = (n) => setSp({ season: String(props.seasonNumber), episode: String(n) }, { replace: false });
// and
useEffect(() => { if (activeEpisode) playerWrapperRef.current?.scrollIntoView({ block: "start" }); }, [activeEpisode?.episode_number]);
```

### F-05 · P1 · `MoviesAndSeriesDetailsSections.jsx:664-834` — no “next episode” affordance anywhere; the player never advances
There is no `onEnded` handler on `InlinePlayer` (`InlinePlayer.jsx:200-224` `plyrProps.options` has no `listeners`), no “Next episode” button in the episode-expanded row, and `MobilePlayBar` (`:846-859`) labels itself only “Play episode”/“Pick episode”. On a binge-watching product this is the single biggest missing interaction — after an episode ends the user must scroll back to the list and find the next row manually.
**Fix:** pass a `listeners.ended` into plyr and render a next-episode CTA:
```jsx
const season = props.movieData.seasons?.find((s) => s.season_number === props.seasonNumber);
const nextEp = season?.episodes?.find((e) => e.episode_number === (activeEpisode?.episode_number ?? 0) + 1);
options: { … },
listeners: { ended: () => nextEp && handlePlayEpisode(nextEp) },
```
and in the player overlay a `Next: E{String(nextEp.episode_number).padStart(2,"0")} · {nextEp.title}` button with `<BsSkipEndFill aria-hidden />`.

### F-06 · P1 · `TvDetails.jsx:63-91` + `:116-128` — the same `/api/id/:id` endpoint is fetched twice per series, and the season fetch has no cancellation
Details effect fetches `${BASE}/api/id/${seriesID}` with no params; the season effect fetches the *same* URL with `?season_number=`. On first paint both fire in parallel. Additionally the season effect has no cleanup, so rapid season-tab taps can resolve out of order and leave the episode list showing the wrong season.
```jsx
// :116-128
useEffect(() => {
  if (seasonNumber === undefined) return;
  setIsEpisodesLoading(true);
  axios.get(`${BASE}/api/id/${seriesID}`, { params: { season_number: seasonNumber } }) …
}, [seasonNumber, seriesID, BASE]);
```
**Fix:** add an abort guard and an error state (today `catch` only clears the spinner, so a failed season is indistinguishable from an empty one — see F-16):
```jsx
useEffect(() => {
  if (seasonNumber === undefined) return;
  const ac = new AbortController();
  setIsEpisodesLoading(true); setEpisodesError(false);
  axios.get(`${BASE}/api/id/${seriesID}`, { params: { season_number: seasonNumber }, signal: ac.signal })
    .then(({ data }) => setEpisodes(data.episodes || []))
    .catch((e) => { if (!axios.isCancel(e)) { setEpisodes([]); setEpisodesError(true); } })
    .finally(() => { if (!ac.signal.aborted) setIsEpisodesLoading(false); });
  return () => ac.abort();
}, [seasonNumber, seriesID, BASE]);
```

### F-07 · P1 · `MoviesAndSeriesDetailsSections.jsx:718` — episode thumbnails are eager `<img>`, up to 50 full-size backdrops per page
```jsx
<img src={eps.episode_backdrop} alt={eps.title} className="w-full h-full object-cover" />
```
50 rows × unstyled TMDB backdrops ≈ several MB on the exact screen mobile users hit after tapping “Series”. Every other poster in the app uses `LazyLoadImage`/`loading="lazy"`.
**Fix:**
```jsx
<img src={eps.episode_backdrop} alt="" loading="lazy" decoding="async" width={112} height={64}
     className="w-full h-full object-cover" />
```
(`alt=""` here — the title is already adjacent text at `:739-742`; repeating it makes the row read twice.)

### F-08 · P1 · `Nav.jsx:512-572` — the mobile search overlay is an undivined `div`: no dialog semantics, unlabelled input, no Escape, no focus trap
```jsx
<motion.div … className="fixed inset-0 z-[60] md:hidden flex flex-col" style={{ background: "#08090B" }}>
  …
  <input ref={mobileInputRef} type="text" name="mobile-search" … placeholder="Search movies, series, anime…" />
```
Body scroll is locked (`:239`) but the overlay is not `role="dialog"` / `aria-modal="true"`, the input has no `<label>` or `aria-label` (placeholder is not an accessible name), there is no `Escape` key handler, focus is not trapped, and focus is not returned to the search button on close. Screen-reader users get an unannounced region and can tab into the page behind it.
**Fix:**
```jsx
<motion.div role="dialog" aria-modal="true" aria-label="Search" onKeyDown={(e) => e.key === "Escape" && close()}>
  <input aria-label="Search movies, series, anime" … />
```
plus `aria-expanded` + `aria-controls` on the trigger at `:381`.

### F-09 · P1 · `CastSection.jsx:66-67` — cast cards explicitly delete the focus ring
```jsx
const wrapperCls =
  "group block w-20 sm:w-24 text-center focus:outline-none focus-visible:outline-none";
```
`index.css:829-833` defines a global `*:focus-visible { outline: 2px solid var(--projector); }`; this utility beats it. Every cast link is unfocusable-but-invisible for keyboard users — a WCAG 2.4.7 failure on the most keyboard-navigable-looking widget in the app.
**Fix:** delete `focus-visible:outline-none` and add a ring that survives the circular crop:
```jsx
"group block w-20 sm:w-24 rounded-lg text-center outline-none focus-visible:ring-2 focus-visible:ring-projector focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
```

### F-10 · P1 · `NotFound.jsx:1-45` + `SEO.jsx:40` — the 404 page has no SEO at all and the app never emits `noindex`
`NotFound.jsx` imports no `SEO`, so it inherits the shell `<title>` / description from `index.html:66,71` and keeps whatever canonical the previous route left. `SEO.jsx` hardcodes `<meta name="robots" content="index, follow" />` for **every** page, including search result pages and cast pages — soft-404s get indexed as thin/duplicate content. (A client-rendered SPA also returns HTTP 200 for unknown URLs; that part needs server/CDN config, not React.)
**Fix:** give `SEO` an `indexable` prop and use it:
```jsx
// SEO.jsx
export default function SEO({ …, noIndex = false }) {
  …
  <meta name="robots" content={noIndex ? "noindex, follow" : "index, follow"} />
```
```jsx
// NotFound.jsx
<SEO title={`Page not found - ${SITENAME}`} description="…" noIndex />
```
Pass `noIndex` from `SearchResults.jsx:52` as well (see F-20).

---

## P2 — UX/architecture defects users will notice

### F-11 · P2 · `MoviesAndSeriesDetailsSections.jsx:579-605` — season selector is a scroll strip of unlabelled buttons; Season 0 disappears but can still be the active season
```jsx
<div className="flex gap-2 overflow-x-auto pb-1 no-scrollbar">
  {props.movieData.seasons
    .filter((s) => s.season_number !== 0)      // :582
    .sort((a, b) => a.season_number - b.season_number)
    .map((season) => (
      <button key={season.season_number} onClick={() => handleSeasonChange(season.season_number)}
        className={…}>Season {season.season_number} ({season.episode_count})</button>
```
Three problems: (a) `TvDetails.jsx:75-77` sets `seasonNumber` to `sortedSeasons[0]`, which for anime/specials-first shows is **0** — a number this list filters out, so the tab strip shows no active tab while episode data for specials is loaded; (b) `no-scrollbar` + `overflow-x-auto` hides *all* affordance that more seasons exist (10-season shows cut off with no fade/chevron); (c) no `role="tablist"`/`role="tab"`/`aria-selected`, so assistive tech reads a row of unrelated buttons.
**Fix:**
```jsx
<div role="tablist" aria-label="Seasons" className="relative -mx-1 flex gap-2 overflow-x-auto px-1 pb-1 no-scrollbar">
  {seasons.filter((s) => s.season_number !== 0 || s.episode_count > 0).map((season) => (
    <button role="tab" aria-selected={isActive} tabIndex={isActive ? 0 : -1}
            onKeyDown={onArrowNav} …>
```
and select `sortedSeasons.find((s) => s.season_number !== 0)?.season_number ?? sortedSeasons[0]?.season_number` in `TvDetails`. Add a right-edge gradient mask so the strip reads as scrollable.

### F-12 · P2 · `MoviesAndSeriesDetailsSections.jsx:162-175, 689-690` — pagination slices *before* sorting, so episode order and page boundaries are wrong
```jsx
// :170-173
const paginatedEpisodes = useMemo(() => {
  const start = (currentPage - 1) * episodesPerPage;
  return filteredEpisodes.slice(start, start + episodesPerPage);
}, [filteredEpisodes, currentPage, episodesPerPage]);
// :689-690  (in JSX)
paginatedEpisodes
  .sort((a, b) => a.episode_number - b.episode_number)
```
Sorting is applied only to the 50 items already sliced out of the *API's* order. If the backend returns episodes in insertion order (common for indexed files), page 1 shows “sorted” episodes 30–42 while page 2 shows 1–8. It also mutates the memoised array during render.
**Fix:** sort inside the memo, before the slice:
```jsx
const sortedEpisodes = useMemo(
  () => [...(props.episodes || [])].sort((a, b) => a.episode_number - b.episode_number),
  [props.episodes]
);
// filter on sortedEpisodes, then slice
```

### F-13 · P2 · `MoviesAndSeriesDetailsSections.jsx:166` — episode search crashes on episodes with a null number
```jsx
(ep) => (ep.title || "").toLowerCase().includes(q)
     || (ep.description || "").toLowerCase().includes(q)
     || ep.episode_number.toString().includes(q)      // TypeError if null/undefined
```
`title`/`description` are guarded; `episode_number` is not. One malformed row in the payload takes down the whole episode list the moment the user types in the search box (and the search box only appears for >10 episodes, so it is discovered mid-session).
**Fix:** `|| String(ep.episode_number ?? "").includes(q)`

### F-14 · P2 · `MoviesAndSeriesDetailsSections.jsx:680-687` — empty/error season state is a bare sentence, inconsistent with the app's `EmptyState`
```jsx
) : paginatedEpisodes.length === 0 ? (
  <div className="py-12 text-center">
    <p className="text-bone/40 text-sm">No episodes found{searchQuery ? ` for "${searchQuery}"` : ""}.</p>
  </div>
) : …
```
Two distinct situations collapse into one message: “this season genuinely has no indexed episodes” (needs a Telegram/notify CTA, matching the “No print available” card at `:394-402`) and “your search matched nothing” (needs a clear-search action). Neither is offered, and the copy is the lowest-contrast text on the page.
**Fix:** branch on `searchQuery` and reuse the existing pattern:
```jsx
) : paginatedEpisodes.length === 0 ? (
  <EmptyState
    icon={FiSearch}
    eyebrow={searchQuery ? "No matches" : "Off season"}
    title={searchQuery ? `Nothing for “${searchQuery}”` : "This season isn't indexed yet"}
    description={searchQuery ? "Try a different title or episode number." : "Join the Telegram channel and we'll announce it."}
    action={searchQuery
      ? <RetryButton label="Clear search" onClick={() => { setSearchQuery(""); setCurrentPage(1); }} />
      : <TelegramButton />}
  />
```

### F-15 · P2 · `MoviesAndSeriesDetailsSections.jsx:459, 564` — overviews are hard-clamped with no “read more” on either breakpoint
```jsx
<p className="… line-clamp-4">{props.movieData.description}</p>   // movie, :459
<p className="… line-clamp-3">{props.movieData.description}</p>   // series, :564
```
Synopses are permanently truncated to 3–4 lines with no expand control, at two different line counts for the same field. The only `showFullDescription` state in the codebase lives in `MobileMovieDetails.jsx:23` — a component that never renders. `line-clamp` itself is fine here (Tailwind 3.4 ships it with the `-webkit-box` prefixes), so the missing piece is the toggle.
**Fix:** extract an `<Overview text clamp={4} />` with an accessible disclosure:
```jsx
const [open, setOpen] = useState(false);
const long = text.length > 280;
<p id="overview" className={open ? "…" : "… line-clamp-4"}>{text}</p>
{long && (
  <button type="button" aria-expanded={open} aria-controls="overview"
          onClick={() => setOpen((o) => !o)}
          className="mt-1 text-[11px] font-semibold uppercase tracking-wider text-projector/80 hover:text-projector">
    {open ? "Show less" : "Read more"}
  </button>
)}
```

### F-16 · P2 · `TvDetails.jsx:127` — a failed season fetch is rendered as “no episodes”, with no retry
`.catch(() => setIsEpisodesLoading(false))` leaves `episodes` as the previous season's array (or `[]`). The UI then shows “No episodes found” (F-14) for a network error, and the only recovery is a full page reload. Every other data surface in the app (`MovieDetails`, `SearchResults`, `MoviesAndSeriesSections`) has an `EmptyState` + `RetryButton` with an online/offline distinction.
**Fix:** add `episodesError` state (see F-06's snippet) and render `<EmptyState title="We lost the season" action={<RetryButton …/>} />`, mirroring `TvDetails.jsx:184-200`.

### F-17 · P2 · `MoviesAndSeriesDetailsSections.jsx:316-322` — the loading skeleton is a single generic block, 300px short of the real layout
```jsx
if (props.isMovieDataLoading) return <div className="mt-4"><DetailSkeleton /></div>;
```
`DetailSkeleton` (`:28-44`) is an `aspect-video` block + 4 text bars. The real page (poster/title/meta/episode list/similar grid) is many screens tall, so the transition from skeleton → content is a large layout jump (CLS) and on mobile the “Play” button appears far below the fold after load. There is no skeleton at all for the episode list (just the spinner at `:680-683`) or for `CastSection`/`UpcomingEpisodeCard`.
**Fix:** make the skeleton structural — same section order and roughly the same heights as the real tree (player box, title lines, meta chip row, season tab row, 6 episode rows at `h-[72px]`, cast circles). Reserve the player with `aspect-video` in both states (already true) and give the episode rows fixed height so nothing shifts.

### F-18 · P2 · `MoviesAndSeriesSections.jsx:28-30` + `SearchResults.jsx:111` — search results render an **empty `<h1>`**
```jsx
<h1 className="section-title text-lg sm:text-xl md:text-2xl tracking-tight capitalize">
  {props.sectionTitle}
</h1>
```
`SearchResults` passes `sectionTitle=""`, so the results page ships a zero-length heading plus the accent bar next to it. Empty headings are announced as “heading level 1, blank” and are an axe/Lighthouse violation; it also means the search page's real h1 is only the quoted term at `SearchResults.jsx:70`.
**Fix:** render the header block conditionally:
```jsx
{props.sectionTitle ? (
  <div className="flex items-center gap-3"><div className="w-px h-7 bg-projector" /><h1 …>{props.sectionTitle}</h1></div>
) : null}
```

### F-19 · P2 · `SearchResults.jsx:113-121`, `CastMedia.jsx:122-129`, `SimilarMov.jsx:57-65` — pagination is component state, never URL state
Every paginated list calls `setCurrentPage(p)` locally. Consequences: Back returns to a *different* page than the one the user was on; a search page can't be bookmarked/shared at page 3; and `SimilarMov`/`SimilarSeries` re-fetch their whole listing from scratch on Back because the component is remounted with `currentPage = 1`.
**Fix:** read/write `?page=` via `useSearchParams` in all three (see F-03 snippet) and keep `Pagination` dumb.

### F-20 · P2 · `SEO.jsx:21-26, 40` — canonical is derived from `window.location` at render time, and search/cast pages are loudly indexable
```jsx
const canonical = typeof window !== "undefined"
  ? `${window.location.origin}${window.location.pathname}` : undefined;
…
<meta name="robots" content="index, follow" />
```
Because canonical reads the *current* location rather than a route-derived value, it is correct only by accident; on the `?page=2` variants that F-19 will introduce it would silently canonicalise away from the paginated view. Combined with a hardcoded `index, follow`, `“oppenheimer” - Search Results` (`SearchResults.jsx:53`) and `…/Cast/123/Benedict%20Cumberbatch` are offered to crawlers as unique content — thin pages that will compete with the actual title pages.
**Fix:** take an explicit `path` prop (falling back to `location.pathname`), and pass `noIndex` from `SearchResults`, `CastMedia` (paginated views), and `NotFound`.

### F-21 · P2 · `SEO.jsx:5-11, 50-53` — no structured data at all, and the Twitter card type is always `summary_large_image`
```jsx
const OG_TYPES = new Set(["website","article","video.movie","video.tv_show","video.episode"]);
…
<meta name="twitter:card" content={imageUrl ? "summary_large_image" : "summary"} />
```
`imageUrl` *always* resolves (it falls back to `/cloudxmovies-logo.svg`, `:30`), so `summary_large_image` is emitted even when no real still exists — a logo stretched to 2:1. And despite `MovieDetails`/`TvDetails` already passing `type="video.movie"` / `video.tv_show`, no `application/ld+json` `Movie`/`TVSeries` block is emitted, so Google can't build a video rich result from any title page.
**Fix:** add a `schema` prop and render `<script type="application/ld+json">{JSON.stringify(schema)}</script>`; in `TvDetails` pass `{ "@context":"https://schema.org", "@type":"TVSeries", name, description, image, numberOfSeasons, aggregateRating: rating && { "@type":"AggregateRating", ratingValue: rating, bestRating: 10 } }`.

### F-22 · P2 · `CastSection.jsx:88, 102` — cast is silently capped at 20 and the count label lies
```jsx
const castList = Array.isArray(cast) ? cast.filter((p) => p?.name).slice(0, 20) : [];
…
<span className="timecode text-[10px] text-bone/40">{String(castList.length).padStart(2, "0")} names</span>
```
A 60-person cast reports “20 names” and offers no “show all”. There is also no pagination/“See full cast” link, and the horizontal strip (`:108`) has `scrollbar-hide` so only 2–3 of the 20 cards are discoverable on desktop. `character` is clamped to one line (`:58`), which truncates things like “Sirius Black (voice)”.
**Fix:** keep the 20-card strip as a teaser but label it honestly and add a disclosure:
```jsx
const total = cast.filter((p) => p?.name).length;
const castList = total > 20 ? cast.filter((p) => p?.name).slice(0, 20) : cast.filter((p) => p?.name);
<span>{castList.length} of {total} names</span>
{total > 20 && <button onClick={() => setExpanded(true)}>Show all {total}</button>}
```

### F-23 · P2 · `CastMedia.jsx:49, 62-69, 97` — generic avatar always, hardcoded brand name, truncated h1, index keys
```jsx
<SEO title={`${displayName} - CloudMovieX Cast`} … />      // :49  brand is CloudXMovies (config.js:9)
<div className="… "><UserRound className="text-projector w-8 h-8" /></div>   // :62  never the actor's photo
<h1 className="… truncate">{displayName}</h1>                                // :67  long names clipped, no title attr
{media.map((movie, index) => (<motion.div key={index} …>                     // :97  index key
```
The person's `profile_path` is available all the way from `CastSection` (`:20`) but is never passed, so every call-sheet page looks identical. `truncate` on the h1 hides names like “Benedict Cumberbatch-Wong” with no `title` and no wrapping. The brand string drifts from `SITENAME`.
**Fix:** pass `?profile=` through the `CastSection` link, `const profile = useSearchParams().get("profile")`, render `<img src={profileUrl(profile)} alt="" loading="lazy" />` with the `UserRound` as fallback; drop `truncate` for `break-words`; use `SITENAME`; key on `movie.tmdb_id`.

### F-24 · P2 · `MobileSearch.jsx` (whole file) + `MobileMovieDetails.jsx` (whole file) — 502 lines of dead, drifted UI, one of them referencing design tokens that do not exist
`grep -rn "MobileSearch"` and `"MobileMovieDetails"` return only their own definitions/exports; neither is imported, routed (`App.jsx:62-80`), or reachable. Meanwhile their behaviour has already diverged from the live implementations: `MobileSearch` trims recent searches to 5 (`:61`) vs Nav's 6 (`Nav.jsx:165`), uses an inline base64 poster placeholder (`:96`) instead of `assets/images/poster-placeholder.png`, and duplicates the trending list that also exists at `Nav.jsx:25`. `MobileMovieDetails` is styled entirely with CSS variables that **do not exist** in `index.css` — `--bg-primary`, `--bg-surface`, `--bg-card`, `--text-primary`, `--text-secondary`, `--text-muted`, `--border-color`, `--accent-primary` (`:41,61,70,84,94,102,229,233`) — versus the real tokens `--ink/--carbon/--bone/--projector/--signal` (`index.css:5-15`). If anyone ever routes it, it renders unstyled and unreadable.
**Fix:** delete both files (and the `safe-area-top` class they depend on, which is also undefined anywhere in `index.css`), or if `MobileSearch` was meant to be the mobile search destination, route it at `/search` and make `Nav.jsx`'s overlay delegate to it instead of re-implementing search a third time.

### F-25 · P2 · `MoviesAndSeriesDetailsSections.jsx:636-639, 846-859` — “upcoming episode” and the sticky play bar don't connect to each other
`UpcomingEpisodeCard` renders only when `next_episode_to_air` exists, as an isolated banner between the quality tabs and the cast — no “remind me”, no link back to the season it belongs to, and its title uses `truncate` (`UpcomingEpisodeCard.jsx:50`) so long titles are cut with no tooltip. Its countdown has no `aria-live`, so a screen-reader user never hears it change, and the 60-second interval (`:28-31`) re-renders the card forever for a value that changes once a minute at most.
**Fix:** announce at coarse granularity, and give the banner an action:
```jsx
<p role="timer" aria-live="off" aria-label={`Airs in ${countdown}`}>…</p>
<Link to={`?season=${episode.season_number}`} className="…">Open Season {episode.season_number}</Link>
```
with `line-clamp-2` instead of `truncate` on the h3.

### F-26 · P2 · `SimilarMov.jsx:34-38` / `SimilarSeries.jsx:33-37` — a second request just to learn the title, and no error state
```jsx
useEffect(() => {
  axios.get(`${BASE}/api/id/${movieID}`).then(({ data }) => setTitle(data.title || "")).catch(() => {});
}, [movieID, BASE]);
```
The “See all similar” page re-fetches the parent title that the detail page already had. Meanwhile the similar-listing effect (`:19-32`) has `catch(() => setIsMoviesDataLoading(false))` with no error flag, so a failed request renders the generic “This shelf is still being stocked” copy (`MoviesAndSeriesSections.jsx:90`) — telling the user the archive has nothing rather than that the request failed, and offering no retry. Also `limit: 18` on the detail page (`MovieDetails.jsx:99`) vs `limit: 20` here means the “See all” grid starts at a different first page than the carousel it came from.
**Fix:** read the title from `location.state` when available (pass it in the `seeMoreButtonLink` `<Link>` at `Similars.jsx:66`), and thread `error`/`onRetry` props into `MoviesAndSeriesSections` — it already supports `props.error` and `props.onRetry` (`:83-107`), they're just never supplied.

### F-27 · P2 · `QualityTab.jsx:32-46` — selected quality is conveyed by colour only, and is a toggle with hidden state
```jsx
<button onClick={() => setSelectedQuality(quality === selectedQuality ? "" : quality)}
  className={`… ${selectedQuality === quality ? "bg-projector border-projector text-ink" : "bg-bone/5 …"}`}>
  {quality}
</button>
```
Gold-vs-grey is the *only* difference (WCAG 1.4.1), there is no `aria-pressed`, and tapping the active chip silently collapses the entire file list — a destructive-feeling action with no affordance. Clicking again to “unselect” is not something users expect from a tab strip.
**Fix:**
```jsx
<button type="button" aria-pressed={selectedQuality === quality} onClick={() => setSelectedQuality(quality)} className="…">
  {selectedQuality === quality && <Check aria-hidden className="h-3 w-3" />}
  {quality}
</button>
```
(keep the collapse behaviour but add an explicit “Hide files” toggle in the expanded panel instead.)

### F-28 · P2 · `QualityTab.jsx:18-19, 55-59` — UA sniffing at render time decides the player URL
```jsx
const isAndroid = /android/i.test(navigator.userAgent);
const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
…
let playUrl = downloadUrl;
if (isAndroid) playUrl = `intent:${downloadUrl}#Intent;…`;
else if (isIOS) playUrl = `vlc://${downloadUrl}`;
```
This runs on every render (cheap but unrepeatable in SSR/tests), and the resulting `intent:`/`vlc://` URL is set as `href` with `target="_blank"` (`:118-127`) — `intent:` URLs cannot be opened via `target=_blank` in Chrome for Android; they must be a same-tab navigation. So the "Player" button silently does nothing on Android, the primary mobile platform.
**Fix:** `window.location.href = playUrl` for `intent:`/`vlc:` schemes, and compute UA once at module scope:
```jsx
const UA = typeof navigator !== "undefined" ? navigator.userAgent : "";
```

---

## P3 — Polish, consistency, code health

### F-29 · P3 · Sub-AA contrast on the densest text in this scope
`.timecode` labels and metadata sit at 9–11px with `text-bone/35…/40` on `#08090B`. Representative offenders in scope:
`MoviesAndSeriesDetailsSections.jsx:674` (`{filteredEpisodes.length} total`, /40), `:740` (`E##` prefix, /35), `:744` (episode synopsis, 12px /40), `QualityTab.jsx:167` (subtitle language, 10px /40), `CastSection.jsx:58` (character, 10px /40), `CastMedia.jsx:71` (title count, /40), `SearchResults.jsx:76` (result count, /40), `MobileSearch.jsx:161,171`.
`bone` at 40% over `#08090B` computes to roughly `#5F5D58` ≈ 3.3:1 — below the 4.5:1 AA threshold for normal text (and these are *small*).
**Fix:** introduce a `text-bone/60` floor for any text under 14px (≈4.8:1) and add a `.meta-quiet` utility token instead of ad-hoc `/35`–`/40` literals, so the floor is enforceable in one place.

### F-30 · P3 · `MovieDetails.jsx:16-44` = `TvDetails.jsx:16-41` — verbatim palette-extraction duplication, no cleanup
`extractPosterColor` is copy-pasted byte-for-byte across the two detail pages, plus two near-identical `useEffect` blocks (`MovieDetails.jsx:62-84` / `TvDetails.jsx:63-91`) that also duplicate the `window.scrollTo(0,0)`, the ambient reset and the `tagVisit` call. Two bugs live in the copy: `img.onload` is never cancelled if the component unmounts (setting state on an unmounted tree), and the comment at `MovieDetails.jsx:37` still says “fallback to brand blue” for a gold brand.
**Fix:** move to `utils/ambientColor.js` with a guard, and share one `useAmbientBackdrop(src)` hook.

### F-31 · P3 · `MoviesAndSeriesDetailsSections.jsx:113, 836-844` — the `Watch` modal is mounted but unreachable
```jsx
const [isWatchMoviePopupOpen, setIsWatchMoviePopupOpen] = useState(false);
…
{props.detailType === "movie" && (
  <Watch isWatchMoviePopupOpen={isWatchMoviePopupOpen} id={props.movieData} … />
)}
```
`setIsWatchMoviePopupOpen` is never called with `true` anywhere (grep-confirmed), so `Watch.jsx` — a 419-line plyr modal — mounts on every movie page and runs its effects for a UI that cannot open. Dead weight in the mobile bundle.
**Fix:** delete the state + `<Watch>` block, or wire the “Play” action to it if the intent was a modal player instead of the inline `InlinePlayer` (they duplicate the same capability).

### F-32 · P3 · `MoviesAndSeriesDetailsSections.jsx:608-620` — episode search appears only past an arbitrary threshold and is not a landmark
`{props.detailType === "series" && totalEpisodes > 10 && ( … <input type="text" placeholder="Search episodes by title or number…" /> )}` — the magic `> 10` means an 11-episode season gets a search box while a 10-episode one doesn't, and the `<input>` has only a placeholder (no `<label>`, no `aria-label`) so it is anonymous to screen readers. It also has no clear button, unlike `MobileSearch.jsx:112-119` and `Nav.jsx:562-570`.
**Fix:** always show it, or gate on a named constant; add `aria-label="Search episodes"` + `type="search"` + a clear button.

### F-33 · P3 · `MoviesAndSeriesDetailsSections.jsx:741-747` — episode titles clamped to one line with no way to read them
```jsx
<h3 className="… line-clamp-1">
  <span className="timecode text-bone/35 mr-1.5">E{String(eps.episode_number).padStart(2,"0")}</span>
  {eps.title}
</h3>
```
Long episode titles (“The One Where Everybody Finds Out”) are cut on mobile with no `title` attribute and no expansion, and the synopsis below it is `hidden sm:block` — so on phones the episode row carries no information beyond a number. The row is also `cursor-default` with no click handler (`:707`), unlike the card-like styling suggests.
**Fix:** `line-clamp-2` + `title={eps.title}`, and make the whole row a `<button>` that calls `handlePlayEpisode(eps)` (the row already visually reads as clickable).

### F-34 · P3 · `MoviesAndSeriesDetailsSections.jsx:129-144` vs `:519-523` — inconsistent genre handling between movie and series
Movies slice to 4 genres (`:419 … .slice(0, 4)`), series render all of them (`:521 props.movieData.genres.map(…)`). A series with 8 TMDB genres wraps to three lines of `.genre-badge` chips and pushes the title down on mobile. Same component, same data, two rules.
**Fix:** pick one — `genres.slice(0, 4)` plus a `+N` badge in both branches.

### F-35 · P3 · `NotFound.jsx:13` — a looping Lottie plays regardless of `prefers-reduced-motion`, and search isn't offered
```jsx
<Lottie animationData={errorAnim} loop autoplay />
```
`index.css:1053-1070` disables CSS animations under reduced motion, but Lottie is JS/SVG driven, so the 404 reel spins forever for users who asked for no motion. The page also offers only “Go Back” + “Home” — no search box, no popular titles — so a user who mistyped `/mov/1234` has no path to what they wanted. And `window.history.back()` (`:26`) navigates off-site when the 404 is the entry point.
**Fix:**
```jsx
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
<Lottie animationData={errorAnim} loop={!reduced} autoplay={!reduced} />
…
onClick={() => (window.history.state?.idx > 0 ? window.history.back() : navigate("/"))}
```
plus the same `<input type="search">` → `/search/:term` form used in `MobileSearch.jsx:104-111`.

---

## Cross-cutting recommendations

1. **One search implementation.** `Nav.jsx` desktop dropdown, `Nav.jsx` mobile overlay, dead `MobileSearch.jsx`, and `SearchResults.jsx` are four surfaces for one feature with four sets of copy (`No results for "x"` / `No results found` / `Nothing in the archive` / `No matches`). Extract a `useSearch()` hook + one `<SearchInput>` + one `<ResultRow>` and let `/search/:term` be the single results surface; the overlay should just push that route.
2. **Heading ownership.** Detail pages get their only `<h1>` from a child (`MoviesAndSeriesDetailsSections.jsx:426, 525`); `MovieDetails.jsx` and `TvDetails.jsx` have none. Move the `<h1>` up to the page and demote the child's to `<h2>`, so the document outline is page → sections → episodes/cast rather than starting mid-tree.
3. **URL as state.** Season, episode, page number and search term are all component state today. Making them URL params fixes deep links (F-04), Back behaviour (F-19), and gives the SEO layer something canonical to point at (F-20) in one change.
4. **Missing back-navigation on desktop.** Neither detail page nor the search/cast pages render a back affordance or breadcrumb; only `NotFound.jsx:25-31` has one. Desktop users (no browser chrome on iOS PWA) are stranded — add `<Breadcrumbs>` to the details header (`Home / Movies / {title}`).
