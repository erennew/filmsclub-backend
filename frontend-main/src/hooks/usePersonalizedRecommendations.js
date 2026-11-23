/**
 * usePersonalizedRecommendations.js
 *
 * Builds a personalized "You May Also Like" list by:
 *  1. Reading the user's watch history from localStorage
 *  2. Extracting their top genres + preferred media type
 *  3. Scoring and re-ranking the backend's similar list to match their taste
 *  4. If watch history is empty, falls back to the backend list as-is
 */
import { useMemo } from "react";
import { getWatchHistory as readStoredHistory } from "../utils/watchHistory";

/**
 * Watch history has exactly one source of truth: `utils/watchHistory.js`,
 * which writes to the "fc_watch_history" key. This hook used to read a
 * DIFFERENT key ("watchHistory") that the live tracking code never writes —
 * only the unmounted WatchHistoryProvider used it — so the personalisation
 * here always saw an empty history and silently degraded to the plain backend
 * list for every visitor. Delegate to the real store instead.
 */
function getWatchHistory() {
  const history = readStoredHistory();
  return Array.isArray(history) ? history : [];
}

/**
 * Count genre occurrences across the watch history.
 * Returns { genreName: score } sorted by score descending.
 */
function buildGenreProfile(history) {
  const counts = {};
  history.forEach((item) => {
    (item.genres || []).forEach((g) => {
      const genre = typeof g === "string" ? g.trim() : "";
      if (genre) counts[genre] = (counts[genre] || 0) + 1;
    });
  });
  return counts;
}

/**
 * Score a candidate movie against the user's genre profile.
 * Higher = more relevant to this user.
 */
function scoreCandidate(movie, genreProfile) {
  let score = 0;
  (movie.genres || []).forEach((g) => {
    const genre = typeof g === "string" ? g.trim() : "";
    score += genreProfile[genre] || 0;
  });
  // Slight boost for higher-rated content
  score += (movie.rating || 0) * 0.5;
  return score;
}

/**
 * Hook: returns a personalized-ranked version of `similarList`.
 *
 * @param {Array}  similarList       - Movies/shows from backend similar API
 * @param {string} currentTmdbId    - Exclude current movie from results
 * @returns {Array}                   Re-ranked list + personalization metadata
 */
export function usePersonalizedRecommendations(similarList, currentTmdbId) {
  return useMemo(() => {
    if (!similarList || similarList.length === 0) {
      return { items: [], isPersonalized: false, topGenres: [] };
    }

    const history = getWatchHistory();

    // Not enough history — return as-is
    if (history.length < 2) {
      return { items: similarList, isPersonalized: false, topGenres: [] };
    }

    const genreProfile = buildGenreProfile(history);
    const topGenres = Object.entries(genreProfile)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 3)
      .map(([genre]) => genre);

    // Filter out the current item, score, sort
    const scored = similarList
      .filter((m) => String(m.tmdb_id) !== String(currentTmdbId))
      .map((movie) => ({
        ...movie,
        _personalScore: scoreCandidate(movie, genreProfile),
      }))
      .sort((a, b) => b._personalScore - a._personalScore);

    // If all scores are 0, no personalization applied
    const hasPersonalization = scored.some((m) => m._personalScore > 0);

    return {
      items: scored,
      isPersonalized: hasPersonalization,
      topGenres,
    };
  }, [similarList, currentTmdbId]);
}
