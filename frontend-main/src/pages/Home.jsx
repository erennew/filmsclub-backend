/**
 * Home.jsx
 * Drives all homepage content sections through the SmartSection engine.
 *
 * Architecture:
 *  • HeroSlider      — immediate, uses its own data fetch
 *  • TopTenToday     — immediate, uses its own data fetch
 *  • SmartSection[]  — driven by SECTIONS config array
 *      priority:"high" → fetch on mount (first ~5 sections)
 *      priority:"lazy" → IntersectionObserver (everything below the fold)
 *
 * To add / remove / reorder sections:
 *  → Edit src/config/sectionsConfig.js — do NOT touch this file.
 */

import React, { useState, useEffect } from "react";
import { getJSON } from "../utils/apiCache";
import { BASE_URL as BASE, SITENAME } from "../config";
import { toast } from "react-toastify";
import "react-toastify/dist/ReactToastify.css";

import HeroSlider   from "../components/HomeHero";
import TopTenToday  from "../components/TopTenToday";
import SmartSection from "../components/SmartSection";
import SEO          from "../components/SEO";
import SplashScreen from "../components/SplashScreen";

import { SECTIONS } from "../config/sectionsConfig";

export default function Home() {
  /* ── Hero data (immediate, separate from section engine) ─────── */
  const [heroMovies,   setHeroMovies]   = useState([]);
  const [heroLoading,  setHeroLoading]  = useState(true);
  const [heroError,    setHeroError]    = useState(false);

  /* ── Top 10 Today (immediate, uses its own shape) ─────────────── */
  const [topTenData,    setTopTenData]    = useState([]);
  const [topTenLoading, setTopTenLoading] = useState(true);

  /* ── Fresh-content refresh triggered by app launch/resume ─────── */
  const [contentRefresh, setContentRefresh] = useState(0);

  useEffect(() => {
    const refreshContent = () => setContentRefresh((version) => version + 1);
    window.addEventListener("cloudx:content-refresh", refreshContent);
    return () => window.removeEventListener("cloudx:content-refresh", refreshContent);
  }, []);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  /* ── Hero fetch: curated picks, latest releases when none are pinned ── */
  useEffect(() => {
    let cancelled = false;
    setHeroLoading(true);
    setHeroError(false);
    const latestReleases = () =>
      getJSON(BASE, "/api/movies", { sort_by: "release_year:desc", page: 1, page_size: 12 });

    /*
     * Fall back to latest releases for ANY hero failure. This previously only
     * handled a 404 while the backend raises 400 on a hero error, so a failed
     * hero request left `heroMovies` empty, HeroSlider returned null and the
     * page opened with no hero at all and no explanation.
     */
    getJSON(BASE, "/api/hero")
      .catch(() => latestReleases())
      .then(({ data }) => {
        if (cancelled) return;
        const picks = data.movies || data.tv_shows || [];
        setHeroMovies(picks);
        if (picks.length === 0) setHeroError(true);
        setHeroLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setHeroError(true);
        setHeroLoading(false);
      });

    return () => { cancelled = true; };
  }, [BASE, contentRefresh]);

  /* ── Top 10 fetch ────────────────────────────────────────────── */
  useEffect(() => {
    let cancelled = false;
    setTopTenLoading(true);

    const rank = (data) => [
      ...(data?.movies || []).map((item) => ({ ...item, media_type: item.media_type || "movie" })),
      ...(data?.tv_shows || []).map((item) => ({ ...item, media_type: item.media_type || "tvshow" })),
    ].sort((a, b) => (b.view_count || 0) - (a.view_count || 0));

    getJSON(BASE, "/api/trending/today", { limit: 10 })
      .then(({ data }) => {
        // Only real "today" view counts belong here. If fewer than ten titles
        // have been watched today, show those — never backfill with all-time
        // favorites, which would make the ranking inaccurate.
        if (!cancelled) setTopTenData(rank(data).slice(0, 10));
      })
      .catch(() => {
        if (!cancelled) setTopTenData([]);
      })
      .finally(() => {
        if (!cancelled) setTopTenLoading(false);
      });

    return () => { cancelled = true; };
  }, [BASE, contentRefresh]);

  return (
    <div>
      {/* Keep the launch surface up until the above-the-fold content is usable.
          Below-fold shelves remain lazy and continue loading after the first view. */}
      <SplashScreen ready={!heroLoading && !topTenLoading} />

      <SEO
        title={`${SITENAME} – Stream Movies, Series & Anime`}
        description={`Watch HD movies, TV series, anime, and K-Dramas on ${SITENAME}. Trending content, top-rated picks, and personalized recommendations.`}
        name={SITENAME}
        type="website"
        keywords="watch movies online, HD streaming, TV series, anime, k-drama, trending movies, top rated"
      />

      {/* ── HERO CAROUSEL ─────────────────────────────────────────── */}
      <HeroSlider
        movieData={heroMovies}
        isMovieDataLoading={heroLoading}
        hasError={heroError}
      />

      {/* ── TOP 10 TODAY ──────────────────────────────────────────── */}
      <TopTenToday
        data={topTenData}
        isLoading={topTenLoading}
        sectionTitle="Top 10 Today"
      />

      {/* ── ALL CONTENT SECTIONS (config-driven) ──────────────────── */}
      {SECTIONS.map((section) => (
        <SmartSection
          key={section.id}
          config={section}
          BASE={BASE}
          refreshVersion={contentRefresh}
        />
      ))}
    </div>
  );
}
