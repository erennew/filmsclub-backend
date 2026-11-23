/**
 * sectionsConfig.js
 * Single source of truth for all homepage sections.
 * Each section defines how to fetch data, what to display, and where to link.
 *
 * fetch(BASE) → Promise<Array>  (must always resolve, never reject to caller)
 * title       → string
 * id          → unique key (used as React key + dedup guard)
 * seeMoreLink → optional route string
 * priority    → 'high' loads immediately | 'lazy' uses IntersectionObserver
 * icon        → optional emoji prefix for section title
 */

import { getJSON } from "../utils/apiCache";

// ── Helpers ──────────────────────────────────────────────────────
/*
 * Every section fetch used to be wrapped in `safe(promise)` = `.catch(() => [])`,
 * which turned a backend outage into "no results" — SmartSection then unmounted
 * the row, so a failed homepage rendered blank with no message. Errors are now
 * allowed to propagate so SmartSection can show an error state with a retry and
 * keep it visually distinct from a genuinely empty shelf.
 */
const safe = (promise) => promise;

const fetchMovies = (BASE, params) =>
  safe(
    getJSON(BASE, "/api/movies", { page: 1, page_size: 20, ...params })
      .then(({ data }) => (data.movies || []).slice(0, 20))
  );

const fetchTV = (BASE, params) =>
  safe(
    getJSON(BASE, "/api/tvshows", { page: 1, page_size: 20, ...params })
      .then(({ data }) => (data.tv_shows || []).slice(0, 20))
  );

const fetchNewEpisodes = (BASE) =>
  safe(
    getJSON(BASE, "/api/new-episodes", { page: 1, page_size: 12 }).then(({ data }) =>
      (data.tv_shows || [])
        .filter((show) => show.latest_episode)
        .map((show) => {
          const episode = show.latest_episode;
          return {
            tmdb_id: show.tmdb_id,
            title: show.title,
            poster: show.poster || show.backdrop,
            rating: show.rating,
            release_year: show.release_year,
            media_type: "tv",
            episode_label: `S${String(episode.season_number).padStart(2, "0")} E${String(episode.episode_number).padStart(2, "0")}`,
            episode_title: episode.title || null,
          };
        })
    )
  );

const fetchTrendingWeek = (BASE) =>
  safe(
    getJSON(BASE, "/api/trending/week", { limit: 20 })
      .then(({ data }) => {
        const all = [...(data.movies || []), ...(data.tv_shows || [])];
        return all.sort((a, b) => (b.view_count || 0) - (a.view_count || 0));
      })
  );

const fetchMostViewed = (BASE) =>
  safe(
    getJSON(BASE, "/api/most-viewed", { limit: 20 })
      .then(({ data }) => {
        const all = [...(data.movies || []), ...(data.tv_shows || [])];
        return all.sort((a, b) => (b.view_count || 0) - (a.view_count || 0));
      })
  );

/* Same source, but only the movie half — the API already separates them, so
 * filter the buckets rather than inspecting per-document media_type, which the
 * views aggregation does not necessarily set on the returned doc. */
const fetchMostViewedMovies = (BASE) =>
  safe(
    getJSON(BASE, "/api/most-viewed", { limit: 20 })
      .then(({ data }) =>
        [...(data.movies || [])].sort(
          (a, b) => (b.view_count || 0) - (a.view_count || 0)
        )
      )
  );

const fetchMostViewedTV = (BASE) =>
  safe(
    getJSON(BASE, "/api/most-viewed", { limit: 20 })
      .then(({ data }) =>
        [...(data.tv_shows || [])].sort(
          (a, b) => (b.view_count || 0) - (a.view_count || 0)
        )
      )
  );

const fetchAnime = (BASE) =>
  safe(
    getJSON(BASE, "/api/anime", { page: 1, page_size: 20, sort_by: "rating:desc" })
      .then(({ data }) => {
        const results = data.results || data.anime || [];
        return results.sort((a, b) => (b.rating || 0) - (a.rating || 0));
      })
  );

const fetchKDrama = (BASE) =>
  safe(
    getJSON(BASE, "/api/k-drama", { page: 1, page_size: 20, sort_by: "rating:desc" })
      .then(({ data }) => {
        const results = data.kdrama || data.tv_shows || data.results || [];
        return results.sort((a, b) => (b.rating || 0) - (a.rating || 0));
      })
  );

// Continue Watching — reads from localStorage visit history
const fetchContinueWatching = () => {
  try {
    const raw = localStorage.getItem("fc_watch_history");
    const history = raw ? JSON.parse(raw) : [];
    // Sort newest first, max 20 items
    return history
      .sort((a, b) => (b.visitedAt || 0) - (a.visitedAt || 0))
      .slice(0, 20);
  } catch {
    return [];
  }
};

// ── Section Definitions ─────────────────────────────────────────
// priority: "high" → load immediately on mount
//           "lazy" → load when within 400px of viewport
/*
 * Only ONE shelf is eager.
 *
 * First load used to fire hero + Top 10 + eight priority:"high" shelves at once
 * (ten concurrent requests) against a backend that fans out across several DB
 * clusters, so everything — including the hero — queued behind a burst of
 * below-the-fold work. "trending-now" is the only shelf that is reliably just
 * under the hero/Top 10 pair, so it is the only one kept eager. Everything
 * else, including the cheap localStorage-backed Continue Watching row (its
 * skipIfEmpty already hides it when empty), now waits for the
 * IntersectionObserver's 400px look-ahead. Ordering and ids are unchanged.
 */
export const SECTIONS = [
  // ── PRIORITY HIGH (above-fold) ─────────────────────────────
  {
    id: "trending-now",
    title: "🔥 Trending Now",
    priority: "high",
    seeMoreLink: null,
    fetch: (BASE) => fetchTrendingWeek(BASE),
  },
  {
    id: "continue-watching",
    title: "▶ Continue Watching",
    priority: "lazy",
    seeMoreLink: null,
    fetch: () => Promise.resolve(fetchContinueWatching()),
    skipIfEmpty: true, // skip skeleton entirely if history is empty
  },
  {
    id: "new-episodes",
    title: "New Episodes",
    priority: "lazy",
    seeMoreLink: null,
    fetch: (BASE) => fetchNewEpisodes(BASE),
  },
  {
    id: "most-watched",
    title: "👁️ Most Watched",
    priority: "lazy",
    seeMoreLink: "/most-viewed",
    fetch: (BASE) => fetchMostViewed(BASE),
  },
  {
    id: "latest-movies",
    title: "Latest Movies",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) => fetchMovies(BASE, { sort_by: "updated_on:desc" }),
  },
  {
    id: "latest-tv",
    title: "Latest TV Shows",
    priority: "lazy",
    seeMoreLink: "/Series",
    fetch: (BASE) => fetchTV(BASE, { sort_by: "updated_on:desc" }),
  },
  {
    id: "popular-movies",
    title: "Popular Movies",
    priority: "lazy",
    seeMoreLink: "/most-viewed",
    // "Popular" means most-watched, not highest-rated. This previously fetched
    // { sort_by: "rating:desc" } — byte-identical to the "Top Rated Movies"
    // section below it — so the homepage rendered the same 20 films twice under
    // two different headings.
    fetch: (BASE) => fetchMostViewedMovies(BASE),
  },

  // ── PRIORITY LAZY (below-fold) ─────────────────────────────
  {
    id: "popular-tv",
    title: "Popular TV Shows",
    priority: "lazy",
    seeMoreLink: "/most-viewed",
    // Was identical to "Top Rated TV Shows" (both rating:desc).
    fetch: (BASE) => fetchMostViewedTV(BASE),
  },
  {
    id: "top-rated-movies",
    title: "Top Rated Movies",
    priority: "lazy",
    seeMoreLink: "/top-rated-movies",
    fetch: (BASE) => fetchMovies(BASE, { sort_by: "rating:desc" }),
  },
  {
    id: "top-rated-tv",
    title: "Top Rated TV Shows",
    priority: "lazy",
    seeMoreLink: "/top-rated-series",
    fetch: (BASE) => fetchTV(BASE, { sort_by: "rating:desc" }),
  },
  {
    id: "action",
    title: "⚡ Action Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Action",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Action", sort_by: "rating:desc" }),
  },
  {
    id: "adventure",
    title: "🗺️ Adventure Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Adventure",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Adventure", sort_by: "rating:desc" }),
  },
  {
    id: "comedy",
    title: "😂 Comedy Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Comedy",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Comedy", sort_by: "rating:desc" }),
  },
  {
    id: "crime",
    title: "🔍 Crime Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Crime",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Crime", sort_by: "rating:desc" }),
  },
  {
    id: "drama",
    title: "🎭 Drama Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Drama",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Drama", sort_by: "rating:desc" }),
  },
  {
    id: "horror",
    title: "👻 Horror Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Horror",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Horror", sort_by: "rating:desc" }),
  },
  {
    id: "thriller",
    title: "🔪 Thriller Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Thriller",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Thriller", sort_by: "rating:desc" }),
  },
  {
    id: "romance",
    title: "❤️ Romance Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Romance",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Romance", sort_by: "rating:desc" }),
  },
  {
    id: "scifi",
    title: "🚀 Sci-Fi Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Science%20Fiction",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Science Fiction", sort_by: "rating:desc" }),
  },
  {
    id: "fantasy",
    title: "🧙 Fantasy Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Fantasy",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Fantasy", sort_by: "rating:desc" }),
  },
  {
    id: "mystery",
    title: "🕵️ Mystery Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Mystery",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Mystery", sort_by: "rating:desc" }),
  },
  {
    id: "animation",
    title: "🎨 Animation Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Animation",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Animation", sort_by: "rating:desc" }),
  },
  {
    id: "family",
    title: "👨‍👩‍👧 Family Movies",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Family",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Family", sort_by: "rating:desc" }),
  },
  {
    id: "documentary",
    title: "🎥 Documentaries",
    priority: "lazy",
    seeMoreLink: "/genre/movie/Documentary",
    fetch: (BASE) => fetchMovies(BASE, { genre: "Documentary", sort_by: "rating:desc" }),
  },
  {
    id: "anime",
    title: "⛩️ Anime",
    priority: "lazy",
    seeMoreLink: "/anime",
    fetch: (BASE) => fetchAnime(BASE),
  },
  {
    id: "kdrama",
    title: "🇰🇷 Korean Drama",
    priority: "lazy",
    seeMoreLink: "/k-drama",
    fetch: (BASE) => fetchKDrama(BASE),
  },
  {
    id: "indian-movies",
    title: "🇮🇳 Indian Movies",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) => fetchMovies(BASE, { language: "hi", sort_by: "rating:desc" }),
  },
  {
    id: "hollywood",
    title: "🎬 Hollywood Movies",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) => fetchMovies(BASE, { language: "en", sort_by: "rating:desc" }),
  },
  {
    id: "south-indian",
    title: "🎞️ South Indian Movies",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) => fetchMovies(BASE, { language: "ta", sort_by: "rating:desc" }),
  },
  {
    id: "korean-movies",
    title: "🇰🇷 Korean Movies",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) => fetchMovies(BASE, { language: "ko", sort_by: "rating:desc" }),
  },
  {
    id: "award-winning",
    title: "🏅 Award Winning",
    priority: "lazy",
    seeMoreLink: "/top-rated-movies",
    fetch: (BASE) => fetchMovies(BASE, { sort_by: "rating:desc", min_rating: 8 }),
  },
  {
    id: "hidden-gems",
    title: "💎 Hidden Gems",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) => fetchMovies(BASE, { sort_by: "rating:desc", min_rating: 7.5, page: 3 }),
  },
  {
    id: "recently-added",
    title: "🆕 Recently Added",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) =>
      Promise.all([
        fetchMovies(BASE, { sort_by: "updated_on:desc" }),
        fetchTV(BASE,    { sort_by: "updated_on:desc" }),
      ]).then(([movies, tv]) => {
        const all = [...movies, ...tv];
        // Interleave by recency; keep both movies and TV
        return all.sort((a, b) => {
          const ta = Date.parse(a.updated_on) || (a.release_year || 0);
          const tb = Date.parse(b.updated_on) || (b.release_year || 0);
          return tb - ta;
        });
      }),
  },
  {
    id: "recommended",
    title: "✨ Recommended For You",
    priority: "lazy",
    seeMoreLink: null,
    fetch: (BASE) =>
      Promise.all([
        fetchMovies(BASE, { sort_by: "rating:desc", page: 2 }),
        fetchTV(BASE,    { sort_by: "rating:desc", page: 2 }),
      ]).then(([movies, tv]) => {
        // Mix movies + TV, shuffle pseudo-randomly
        const all = [...movies, ...tv].sort(() => Math.random() - 0.5);
        return all.slice(0, 20);
      }),
  },
  {
    id: "international",
    title: "🌍 International Movies",
    priority: "lazy",
    seeMoreLink: "/Movies",
    fetch: (BASE) => fetchMovies(BASE, { sort_by: "rating:desc", page: 4 }),
  },
];
