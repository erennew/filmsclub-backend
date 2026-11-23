import React from "react";
import { motion } from "framer-motion";
import MovieCard from "./MovieCard";
import MovieCardSkeleton from "./MovieCardSkeleton";
import EmptyState, { RetryButton } from "./EmptyState";
import useOnlineStatus from "../hooks/useOnlineStatus";
import { HiOutlineAdjustmentsHorizontal } from "react-icons/hi2";
import { FiFilm, FiWifiOff } from "react-icons/fi";

const FILTER_OPTIONS = [
  { name: "Latest", value: "updated_on" },
  { name: "Top Rated", value: "rating" },
  { name: "Newest", value: "release_year" },
];

export default function MoviesAndSeriesSections(props) {
  const online = useOnlineStatus();
  const items = props.movieData || [];

  return (
    <div>
      {/* Section Header + Filters */}
      <div className="mt-8 mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          {/* Title */}
          <div className="flex items-center gap-3">
            <div className="w-px h-7 bg-projector shrink-0" />
            <h1 className="section-title text-lg sm:text-xl md:text-2xl tracking-tight capitalize">
              {props.sectionTitle}
            </h1>
          </div>

          {/* Filters */}
          {(props.dataType === "movies" || props.dataType === "series") && (
            <div className="flex items-center gap-2">
              <HiOutlineAdjustmentsHorizontal className="text-bone/60 text-lg shrink-0" />
              <div className="flex items-center gap-1.5 bg-bone/5 border border-bone/10 rounded-[3px] p-1">
                {FILTER_OPTIONS.map((item) => {
                  const isActive = item.value === props.movieFilterVal;
                  return (
                    <button
                      key={item.value}
                      onClick={() => {
                        props.setMovieFilterVal(item.value);
                        props.setMovieFilter(item.value);
                      }}
                      className={`
                        relative px-3 sm:px-4 py-1.5 rounded-[3px] text-xs sm:text-sm font-semibold
                        transition-all duration-200 whitespace-nowrap
                        ${isActive
                          ? "text-ink"
                          : "text-bone/50 hover:text-bone/80"
                        }
                      `}
                    >
                      {isActive && (
                        <motion.div
                          layoutId="filterPill"
                          className="absolute inset-0 bg-projector rounded-[3px]"
                          style={{ zIndex: -1 }}
                          transition={{ type: "spring", stiffness: 500, damping: 40 }}
                        />
                      )}
                      {item.name}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Divider */}
        <div className="mt-4 h-px bg-gradient-to-r from-projector/20 via-bone/5 to-transparent" />
      </div>

      {/* Grid of cards */}
      {!props.isMovieDataLoading ? (
        items.length === 0 ? (
          <EmptyState
            icon={online ? FiFilm : FiWifiOff}
            eyebrow={
              !online ? "Signal lost" : props.error ? "Projection interrupted" : "House lights"
            }
            title={
              !online
                ? "You're offline"
                : props.error
                  ? "We lost the feed"
                  : "This shelf is still being stocked"
            }
            description={
              !online
                ? "This shelf hasn't been cached yet. Reconnect to load the latest arrivals."
                : props.error
                  ? "The archive didn't answer in time. Give it another reel."
                  : "Nothing is screening here yet — check back after the next reel is loaded."
            }
            action={
              !online ? (
                <RetryButton
                  onClick={() => window.location.reload()}
                  label="Reload shelf"
                />
              ) : props.error && props.onRetry ? (
                <RetryButton onClick={props.onRetry} label="Retry shelf" />
              ) : null
            }
          />
        ) : (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.3 }}
            className="grid gap-x-2 sm:gap-x-3 gap-y-6 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7"
          >
            {items.map((movie, index) => (
              <motion.div
                /* Index keys made React reuse the wrong card when the filter or
                   page changed — poster and links briefly belonged to a
                   different title. Key on identity instead. */
                key={`${movie.media_type ?? "m"}:${movie.tmdb_id ?? movie._id ?? index}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(index * 0.03, 0.5) }}
              >
                <MovieCard movie={movie} />
              </motion.div>
            ))}
          </motion.div>
        )
      ) : (
        <MovieCardSkeleton count={20} />
      )}
    </div>
  );
}
