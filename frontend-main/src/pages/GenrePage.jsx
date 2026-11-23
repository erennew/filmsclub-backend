// src/pages/GenrePage.jsx
// Unified genre browsing page — handles both movies and TV shows
// Route: /genre/:mediaType/:genreName  (mediaType = "movie" | "tv")
import React, { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { useParams, useNavigate } from "react-router-dom";
import { BASE_URL as BASE, SITENAME } from "../config";
import MovieCard from "../components/MovieCard";
import MovieCardSkeleton from "../components/MovieCardSkeleton";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

const SORT_OPTIONS = [
  { value: "rating", label: "Top Rated" },
  { value: "updated_on", label: "Latest Added" },
  { value: "release_year", label: "Release Year" },
];

export default function GenrePage() {
  const { mediaType = "movie", genreName } = useParams();
  const navigate = useNavigate();
  const decodedGenre = decodeURIComponent(genreName || "");

  // Active media type tab — "movie" or "tv"
  const [activeType, setActiveType] = useState(mediaType === "tv" ? "tv" : "movie");

  const [items, setItems] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [sortBy, setSortBy] = useState("rating");

  const PAGE_SIZE = 24;

  // Sync URL when tab changes
  const switchType = (type) => {
    setActiveType(type);
    setCurrentPage(1);
    navigate(`/genre/${type}/${encodeURIComponent(decodedGenre)}`, { replace: true });
  };

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    setError(false);
    setItems([]);
    window.scrollTo({ top: 0, behavior: "smooth" });

    try {
      const endpoint = activeType === "movie" ? "/api/movies" : "/api/tvshows";
      const params = {
        genre: decodedGenre,
        sort_by: `${sortBy}:desc`,
        page: currentPage,
        page_size: PAGE_SIZE,
      };

      const { data } = await axios.get(`${BASE}${endpoint}`, { params });
      let results = activeType === "movie" ? data.movies || [] : data.tv_shows || [];

      // Client-side genre filter as safety net (backend may return broader matches)
      const target = decodedGenre.toLowerCase();
      results = results.filter((item) => {
        if (!item.genres?.length) return false;
        const genresArr = Array.isArray(item.genres) ? item.genres : [item.genres];
        return genresArr.some(
          (g) => typeof g === "string" && g.toLowerCase().includes(target)
        );
      });

      setItems(results);
      setTotalCount(data.total_count || results.length);
    } catch (err) {
      console.error("Genre fetch error:", err);
      setError(true);
      setItems([]);
    } finally {
      setIsLoading(false);
    }
  }, [activeType, decodedGenre, sortBy, currentPage, BASE]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Reset page when sort or type changes
  useEffect(() => {
    setCurrentPage(1);
  }, [sortBy, activeType]);

  const pageTitle = `${decodedGenre} ${activeType === "movie" ? "Movies" : "TV Shows"}`;

  return (
    <div className="min-h-screen">
      <SEO
        title={`${pageTitle} - ${SITENAME}`}
        description={`Browse ${pageTitle} on ${SITENAME}. Discover the best ${decodedGenre.toLowerCase()} content sorted by rating, release year, and more.`}
        name={SITENAME}
        type="website"
        keywords={`${decodedGenre} movies, ${decodedGenre} TV shows, stream ${decodedGenre} online`}
      />

      {/* Genre Header */}
      <div className="mb-6">
        <span className="eyebrow mb-3">Genre index</span>
        <h1 className="section-title text-2xl sm:text-3xl">{decodedGenre}</h1>
        <p className="timecode text-[11px] text-bone/60 mt-1.5">
          {isLoading ? "Loading…" : `${totalCount.toLocaleString()} titles`}
        </p>

        {/* Tabs + Sort Row */}
        <div className="flex flex-wrap items-center justify-between gap-3 mt-5">
          {/* Media type tabs */}
          <div className="flex bg-bone/[0.04] border border-bone/10 rounded-[3px] p-1 gap-1">
            <button
              onClick={() => switchType("movie")}
              className={`px-4 py-1.5 rounded-[3px] text-sm font-medium transition-all duration-200 ${
                activeType === "movie"
                  ? "bg-projector text-ink"
                  : "text-bone/60 hover:text-bone"
              }`}
            >
              Movies
            </button>
            <button
              onClick={() => switchType("tv")}
              className={`px-4 py-1.5 rounded-[3px] text-sm font-medium transition-all duration-200 ${
                activeType === "tv"
                  ? "bg-projector text-ink"
                  : "text-bone/60 hover:text-bone"
              }`}
            >
              TV Shows
            </button>
          </div>

          {/* Sort selector */}
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-wider text-bone/55 hidden sm:inline">
              Sort by
            </span>
            <div className="flex bg-bone/[0.04] border border-bone/10 rounded-[3px] p-1 gap-1">
              {SORT_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setSortBy(opt.value)}
                  className={`px-3 py-1 rounded-[3px] text-xs font-medium transition-all duration-200 ${
                    sortBy === opt.value
                      ? "bg-projector text-ink"
                      : "text-bone/60 hover:text-bone"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-4 h-px bg-gradient-to-r from-projector/20 via-bone/5 to-transparent" />
      </div>

      {/* Content Grid */}
      {isLoading ? (
        <MovieCardSkeleton count={PAGE_SIZE} />
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <span className="eyebrow mb-3">Signal lost</span>
          <h2 className="font-display text-xl text-bone/90 mb-2">
            Couldn&rsquo;t load {decodedGenre} {activeType === "movie" ? "movies" : "TV shows"}
          </h2>
          <p className="text-bone/50 text-sm max-w-md leading-relaxed">
            The catalog didn&rsquo;t respond. Check your connection and try again.
          </p>
          <button
            onClick={fetchData}
            className="mt-5 px-5 py-2.5 min-h-[44px] rounded-[3px] bg-projector hover:bg-[#F0CA98] text-ink text-sm font-bold transition-colors"
          >
            Retry
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <span className="eyebrow mb-3">No screenings</span>
          <h2 className="font-display text-xl text-bone/90 mb-2">
            No {decodedGenre} {activeType === "movie" ? "movies" : "TV shows"} yet
          </h2>
          <p className="text-bone/50 text-sm max-w-md leading-relaxed">
            This shelf is still being programmed. Check the other format, or come back after the next update.
          </p>
          <button
            onClick={() => switchType(activeType === "movie" ? "tv" : "movie")}
            className="mt-5 px-5 py-2.5 min-h-[44px] rounded-[3px] bg-projector hover:bg-[#F0CA98] text-ink text-sm font-bold transition-colors"
          >
            Try {activeType === "movie" ? "TV Shows" : "Movies"} instead
          </button>
        </div>
      ) : (
        <div className="grid gap-x-2 sm:gap-x-3 gap-y-6 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7">
          {items.map((item) => (
            <MovieCard key={item.tmdb_id || item._id} movie={item} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {!isLoading && items.length > 0 && (
        <Pagination
          currentPage={currentPage}
          total={totalCount}
          onPageChange={(p) => {
            setCurrentPage(p);
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
          limit={PAGE_SIZE}
        />
      )}
    </div>
  );
}
