import React from "react";
import { Link } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import MovieCard from "./MovieCard";
import MovieCardSkeleton from "./MovieCardSkeleton";
import { HiArrowRight, HiSparkles } from "react-icons/hi2";
import { usePersonalizedRecommendations } from "../hooks/usePersonalizedRecommendations";

/**
 * Similars — "You May Also Like" section with personalized ranking.
 *
 * When the user has enough watch history (≥2 items), results are
 * re-ranked by their genre preferences. A "Picked for you" badge
 * appears so users know it's personalized.
 */
export default function Similars({ movieData, isMovieDataLoading, sectionTitle, seeMoreButtonLink, currentTmdbId }) {
  const { items, isPersonalized, topGenres } = usePersonalizedRecommendations(
    movieData,
    currentTmdbId
  );

  if (!isMovieDataLoading && (!items || items.length === 0)) {
    return null;
  }

  return (
    <section className="mt-12 md:mt-16">
      {/* ── Section Header ─────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-5 gap-4">
        <div className="flex items-center gap-3 min-w-0">
          {/* Accent bar */}
          <div className="w-px h-5 bg-projector shrink-0" />

          <div className="min-w-0">
            <h2 className="section-title text-base sm:text-lg md:text-xl tracking-tight truncate">
              {sectionTitle || "You May Also Like"}
            </h2>

            {/* Personalized label */}
            <AnimatePresence>
              {isPersonalized && (
                <motion.div
                  key="personalized-badge"
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.3 }}
                  className="flex items-center gap-1.5 mt-0.5"
                >
                  <HiSparkles className="text-[10px] text-otherColor shrink-0" />
                  <span className="text-[10px] text-otherColor/80 font-medium">
                    Picked for you
                    {topGenres.length > 0 && (
                      <span className="text-bone/55 font-normal">
                        {" "}· based on {topGenres.slice(0, 2).join(" & ")}
                      </span>
                    )}
                  </span>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>

        {seeMoreButtonLink && (
          <Link
            to={seeMoreButtonLink}
            className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider text-bone/50 hover:text-projector transition-colors duration-200 shrink-0 group"
            style={{ textDecoration: "none" }}
          >
            <span>See all</span>
            <HiArrowRight className="text-sm group-hover:translate-x-1 transition-transform duration-200" />
          </Link>
        )}
      </div>

      {/* ── Cards Grid ──────────────────────────────────────────────── */}
      {!isMovieDataLoading ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="grid gap-x-2 sm:gap-x-3 gap-y-6 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7"
        >
          {items.map((movie, index) => (
            <motion.div
              key={movie.tmdb_id || index}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(index * 0.04, 0.4) }}
            >
              <MovieCard movie={movie} />
            </motion.div>
          ))}
        </motion.div>
      ) : (
        <MovieCardSkeleton count={10} />
      )}
    </section>
  );
}
