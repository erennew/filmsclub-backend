/**
 * watchHistory.js
 * Lightweight localStorage-based watch history for "Continue Watching".
 * Call trackVisit(movie) whenever a user opens a movie/series detail page.
 */

const KEY     = "fc_watch_history";
const MAX     = 30; // keep last 30 items

/**
 * Add or update an item in watch history.
 * @param {object} item  – any movie/series object with at minimum { tmdb_id, title }
 */
export function trackVisit(item) {
  if (!item?.tmdb_id) return;
  try {
    const raw     = localStorage.getItem(KEY);
    let history   = raw ? JSON.parse(raw) : [];

    // Remove existing entry for this id (we'll re-add at front)
    history = history.filter((h) => h.tmdb_id !== item.tmdb_id);

    // Prepend with timestamp
    history.unshift({
      tmdb_id:    item.tmdb_id,
      title:      item.title      || "",
      poster:     item.poster     || item.backdrop || "",
      backdrop:   item.backdrop   || "",
      rating:     item.rating     || null,
      release_year: item.release_year || null,
      media_type: item.media_type || "movie",
      genres:     item.genres     || [],
      rip:        item.rip        || null,
      visitedAt:  Date.now(),
    });

    // Trim to max
    if (history.length > MAX) history = history.slice(0, MAX);

    localStorage.setItem(KEY, JSON.stringify(history));
  } catch {
    // localStorage unavailable (private mode, storage full, etc.)
  }
}

/**
 * Get full watch history (newest first).
 */
export function getWatchHistory() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

/**
 * Clear watch history.
 */
export function clearWatchHistory() {
  try { localStorage.removeItem(KEY); } catch {}
}
