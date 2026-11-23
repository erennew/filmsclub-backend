/*
 * Image URL normalisation.
 *
 * The API hands back TMDB URLs at whatever size it stored them, and in practice
 * that is `image.tmdb.org/t/p/original/...`. Lighthouse on the live site showed
 * the hero backdrop alone weighing 1.5 MB, with a second 1.1 MB image taking
 * 20.4 s — that single choice was the whole reason LCP sat at 24.8 s and
 * performance scored 39.
 *
 * A grid card renders at most ~200 CSS px wide (360/2) and a phone hero at
 * 390–430. Serving `original` (often 2000+ px) wastes bandwidth that costs real
 * seconds on mobile data. These helpers rewrite the size segment of a TMDB URL
 * to the smallest bucket that still looks sharp at 2× DPR, and leave any URL
 * that is not a TMDB path untouched (so custom/self-hosted images pass through
 * unchanged).
 *
 * The sizes are TMDB's documented buckets; asking for one that does not exist
 * 404s, so the mapping is deliberately conservative.
 */

/* TMDB path shape: https://image.tmdb.org/t/p/<size>/<file>.jpg */
const TMDB_RE = /^(https?:\/\/image\.tmdb\.org\/t\/p\/)([^/]+)(\/.+)$/i;

/* Poster buckets, smallest → largest. Cards are 2:3 so ~342 is the practical floor. */
const POSTER_LADDER = ["w92", "w154", "w185", "w342", "w500", "w780"];
/* Backdrops are 16:9; 1280 covers a full-bleed phone hero at 2x comfortably. */
const BACKDROP_LADDER = ["w300", "w780", "w1280"];

function pickBucket(ladder, targetWidth, dpr = 2) {
  /*
   * Pick the smallest bucket that still covers targetWidth at `dpr`.
   *
   * The target widths callers pass are the *largest* a slot gets at any
   * breakpoint, not the phone size, because the same URL is reused on desktop:
   * a grid card is ~104px on a 360px phone but ~180px in a wide desktop grid,
   * and 180 x 2 = 360 still needs w342. Sizing for the phone would go soft on
   * desktop, so the ladder is driven by the worst case. The saving that matters
   * is original (1000-2000px, 0.5-1.5MB) down to the right bucket (~30KB).
   */
  const needed = targetWidth * dpr;
  for (const bucket of ladder) {
    if (Number(bucket.replace("w", "")) >= needed) return bucket;
  }
  return ladder[ladder.length - 1];
}

/**
 * Resize a TMDB image URL to `targetWidth` CSS pixels.
 * Non-TMDB URLs (and empty values) are returned unchanged.
 */
export function sizedImage(url, targetWidth, dpr = 2, ladder = POSTER_LADDER) {
  if (!url || typeof url !== "string") return url;
  const match = url.match(TMDB_RE);
  if (!match) return url;
  const bucket = pickBucket(ladder, targetWidth, dpr);
  return `${match[1]}${bucket}${match[3]}`;
}

/**
 * Poster for a grid card / small thumbnail.
 *
 * The default width is 170 CSS px, not the 200 a card is allowed to reach: the
 * grid is 3 columns at 360px wide with 12px gutters, so a card is ~104px there
 * and ~170px only on a 480px+ phone or a wide desktop tile. Sizing for the
 * common case keeps phones off the 342 bucket entirely (185px × 2 = 370 ≥ 342),
 * which is where most of the bytes are saved.
 */
export function posterUrl(url, width = 170) {
  return sizedImage(url, width, 2, POSTER_LADDER);
}

/** Backdrop for a full-bleed hero or detail banner. */
export function backdropUrl(url, width = 1440) {
  return sizedImage(url, width, 2, BACKDROP_LADDER);
}

/*
 * srcset support: TMDB serves fixed buckets, so instead of `w` descriptors we
 * emit explicit 1x/2x candidates. Browsers that understand srcset pick the 2x
 * candidate on retina screens and the 1x one on standard displays.
 */
export function posterSrcSet(url, width = 200) {
  if (!url || typeof url !== "string" || !TMDB_RE.test(url)) return undefined;
  const one = sizedImage(url, width, 1, POSTER_LADDER);
  const two = sizedImage(url, width, 2, POSTER_LADDER);
  if (one === two) return undefined;
  return `${one} 1x, ${two} 2x`;
}

export function backdropSrcSet(url, width = 1440) {
  if (!url || typeof url !== "string" || !TMDB_RE.test(url)) return undefined;
  const one = sizedImage(url, width, 1, BACKDROP_LADDER);
  const two = sizedImage(url, width, 2, BACKDROP_LADDER);
  if (one === two) return undefined;
  return `${one} 1x, ${two} 2x`;
}

/*
 * Cast headshot.
 *
 * The API has shipped this field under three different shapes over time:
 *   `profile`      -> full URL (e.g. .../t/p/w185/abc.jpg) from metadata.py
 *   `profile_path` -> bare TMDB path ("/abc.jpg") OR null, from the old
 *                     compact_metadata_for_queue()
 *   `profile_url`  -> full URL from older rows
 * A bare path is not loadable on its own, and `null`/`""` mean "no headshot",
 * so normalise all three here instead of letting each caller re-guess. Cards
 * render at 80-96 CSS px, so `w185` is the right bucket even at 2x.
 */
const PROFILE_BASE = "https://image.tmdb.org/t/p/w185";
/* Largest headshot bucket worth shipping: cards top out at 96 CSS px, so 185
 * already covers a 2x screen. Anything bigger is bytes we cannot see. */
const PROFILE_MAX_PX = 185;

export function personImageUrl(person) {
  if (!person) return "";
  const raw =
    person.profile ||
    person.profile_path ||
    person.profile_url ||
    person.avatar ||
    "";
  const value = typeof raw === "string" ? raw.trim() : "";
  if (!value) return "";
  // A bare TMDB path, not a URL - prepend the CDN host.
  if (value.startsWith("/")) return `${PROFILE_BASE}${value}`;
  if (!/^https?:\/\//i.test(value)) return "";
  // A full URL may still point at `original` (or any other oversize bucket).
  // Rewrite only when it is actually too large: re-bucketing an already-correct
  // w185 up to w300 would cost bytes for no visible gain.
  const match = value.match(TMDB_RE);
  if (!match) return value;
  const size = match[2];
  // `original` has no numeric size, so treat it as always oversized.
  if (size === "original") return `${match[1]}w${PROFILE_MAX_PX}${match[3]}`;
  const px = Number(size.replace(/^w/, ""));
  if (!Number.isFinite(px) || px <= PROFILE_MAX_PX) return value;
  return `${match[1]}w${PROFILE_MAX_PX}${match[3]}`;
}

/** True when the URL points at a TMDB bucket larger than `w780` (i.e. `original`). */
export function isOversized(url) {
  if (!url || typeof url !== "string") return false;
  const match = url.match(TMDB_RE);
  if (!match) return false;
  const size = match[2];
  if (size === "original") return true;
  const px = Number(size.replace("w", ""));
  return Number.isFinite(px) && px > 780;
}
