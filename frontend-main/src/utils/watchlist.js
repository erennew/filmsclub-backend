/**
 * watchlist.js
 * localStorage-backed "My List" for titles the viewer wants to watch later.
 * Mirrors watchHistory.js and broadcasts changes so every heart stays in sync.
 */

const KEY = "fc_watchlist";
const MAX = 200;
const EVENT = "fc-watchlist-change";

function mediaTypeOf(item) {
  return item?.media_type === "movie" ? "movie" : "tv";
}

export function watchlistKey(item) {
  if (!item?.tmdb_id) return null;
  return `${mediaTypeOf(item)}:${item.tmdb_id}`;
}

function read() {
  try {
    const raw = localStorage.getItem(KEY);
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function write(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // localStorage unavailable (private mode, storage full, etc.)
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(EVENT));
  }
}

function snapshot(item) {
  return {
    tmdb_id: item.tmdb_id,
    title: item.title || "",
    poster: item.poster || item.backdrop || "",
    backdrop: item.backdrop || "",
    rating: item.rating ?? null,
    release_year: item.release_year || item.year || null,
    media_type: mediaTypeOf(item),
    genres: item.genres || [],
    addedAt: Date.now(),
  };
}

export function getWatchlist() {
  return read();
}

export function isInWatchlist(item) {
  const key = watchlistKey(item);
  if (!key) return false;
  return read().some((entry) => watchlistKey(entry) === key);
}

export function addToWatchlist(item) {
  const key = watchlistKey(item);
  if (!key) return false;
  const list = read().filter((entry) => watchlistKey(entry) !== key);
  list.unshift(snapshot(item));
  write(list.slice(0, MAX));
  return true;
}

export function removeFromWatchlist(item) {
  const key = watchlistKey(item);
  if (!key) return false;
  write(read().filter((entry) => watchlistKey(entry) !== key));
  return true;
}

export function toggleWatchlist(item) {
  return isInWatchlist(item) ? removeFromWatchlist(item) : addToWatchlist(item);
}

export function clearWatchlist() {
  write([]);
}

export function subscribeWatchlist(callback) {
  if (typeof window === "undefined") return () => {};
  const onChange = () => callback();
  const onStorage = (event) => {
    if (event.key === KEY) callback();
  };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}
