/**
 * SmartSection.jsx
 * Universal lazy-loaded section wrapper.
 *
 * • priority="high" → fetches immediately on mount
 * • priority="lazy" → IntersectionObserver with 400px look-ahead
 *
 * Three distinct outcomes, which used to be collapsed into one:
 *   • empty   → the shelf genuinely has nothing matching; hide the row
 *   • failed  → the request errored; keep the row, say so, offer Retry
 *   • loaded  → render the shelf
 *
 * Collapsing "failed" into "empty" was why a backend outage produced a blank
 * homepage with no explanation and no way to recover.
 */

import React, { useRef, useEffect, useState, useCallback } from "react";
import HomeSection from "./HomeSections";

const cleanTitle = (title) => String(title).replace(/^[^\p{L}\p{N}]+/u, "");

/* Reserved height for the not-yet-fetched / loading state, so triggering the
 * IntersectionObserver does not shift everything below it. */
const SKELETON_H = "min-h-[268px]";

function SectionError({ title, onRetry, retrying, attempt }) {
  return (
    <section className="mb-9" aria-live="polite">
      <div className="flex items-center gap-3 mb-4">
        <span aria-hidden="true" className="block h-7 w-px bg-signal/70" />
        <h2 className="section-title text-lg sm:text-xl text-bone/80">
          {cleanTitle(title)}
        </h2>
      </div>
      <div
        role="alert"
        className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-[4px] border border-signal/25 bg-signal/[0.06] px-4 py-4"
      >
        <p className="text-sm text-bone/70">
          {retrying
            ? "Still trying to reach the library…"
            : "Couldn’t load this shelf."}
        </p>
        {/* After the second failure, name the likely cause so the visitor is not
            left guessing whether the site or their connection is at fault. */}
        {!retrying && attempt > 1 && (
          <p className="w-full text-xs text-bone/45">
            The library is taking too long to answer. It may be busy — retrying
            usually works.
          </p>
        )}
        <button
          type="button"
          onClick={onRetry}
          disabled={retrying}
          className="ml-auto inline-flex items-center gap-2 rounded-[3px] border border-bone/20 bg-bone/8 px-3 py-1.5 text-xs font-semibold text-bone/85 transition-colors hover:bg-bone/14 hover:text-bone focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink disabled:cursor-not-allowed disabled:opacity-50"
        >
          {retrying ? "Retrying…" : "Retry"}
        </button>
      </div>
    </section>
  );
}

export default function SmartSection({ config, BASE, refreshVersion = 0 }) {
  const sentinelRef = useRef(null);
  const fetchedRef = useRef(false); // prevents double-fetch across re-renders
  const requestRef = useRef(0);
  const lastRefreshRef = useRef(refreshVersion);

  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  const doFetch = useCallback(
    (force = false) => {
      if (fetchedRef.current && !force) return;
      fetchedRef.current = true;
      const requestId = ++requestRef.current;
      setLoading(true);
      setError(false);
      setAttempt((n) => n + 1);
      Promise.resolve(config.fetch(BASE))
        .then((results) => {
          if (requestId !== requestRef.current) return;
          setData(Array.isArray(results) ? results : []);
          setLoading(false);
          setDone(true);
        })
        .catch(() => {
          if (requestId !== requestRef.current) return;
          setError(true);
          setLoading(false);
          setDone(true);
        });
    },
    [config, BASE]
  );

  useEffect(() => {
    if (config.priority === "high") {
      doFetch();
      return undefined;
    }

    // Lazy: trigger when sentinel enters viewport
    const el = sentinelRef.current;
    if (!el) return undefined;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          doFetch();
          observer.disconnect();
        }
      },
      { rootMargin: "400px 0px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (refreshVersion === lastRefreshRef.current) return;
    lastRefreshRef.current = refreshVersion;
    if (!fetchedRef.current) return;
    setDone(false);
    doFetch(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshVersion]);

  /* Retry keeps the failed row in place and only flips it into its "retrying"
   * wording. Clearing `done` here instead made the row vanish for the duration
   * of the request, so pressing Retry looked like it had done nothing. */
  const retry = () => {
    doFetch(true);
  };

  // ── Render decisions ─────────────────────────────────────────────

  // Failed → keep the row visible with an explanation and a retry.
  if (done && (error || loading)) {
    return (
      <div ref={sentinelRef}>
        <SectionError
          title={config.title}
          onRetry={retry}
          retrying={loading}
          attempt={attempt}
        />
      </div>
    );
  }

  // Fetch done + genuinely empty → invisible (no blank row)
  if (done && data.length === 0) return null;

  // skipIfEmpty sections (e.g. Continue Watching) → stay hidden until data confirmed
  if (config.skipIfEmpty && !done) return null;

  // Lazy not-yet-triggered → sentinel only. It carries the skeleton's height so
  // triggering the fetch does not shove the rest of the page downwards.
  if (!done && !loading && config.priority === "lazy") {
    return (
      <div ref={sentinelRef} className={SKELETON_H} aria-hidden="true" />
    );
  }

  return (
    <div ref={sentinelRef}>
      <HomeSection
        movieData={data}
        isMovieDataLoading={loading}
        sectionTitle={cleanTitle(config.title)}
        sectionSeeMoreButtonLink={config.seeMoreLink || null}
      />
    </div>
  );
}
