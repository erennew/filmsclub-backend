import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { PiHeartStraight } from "react-icons/pi";
import EmptyState from "../components/EmptyState";
import MovieCard from "../components/MovieCard";
import SEO from "../components/SEO";
import { SITENAME } from "../config";
import { clearWatchlist, getWatchlist, subscribeWatchlist } from "../utils/watchlist";

const FILTERS = [
  { name: "All", value: "all" },
  { name: "Movies", value: "movie" },
  { name: "Series", value: "tv" },
];

export default function MyList() {
  const [items, setItems] = useState(getWatchlist);
  const [filter, setFilter] = useState("all");
  const [confirmClear, setConfirmClear] = useState(false);

  useEffect(() => {
    setItems(getWatchlist());
    return subscribeWatchlist(() => setItems(getWatchlist()));
  }, []);

  useEffect(() => {
    if (!confirmClear) return;
    const timer = setTimeout(() => setConfirmClear(false), 4000);
    return () => clearTimeout(timer);
  }, [confirmClear]);

  const visible = useMemo(
    () => (filter === "all" ? items : items.filter((item) => item.media_type === filter)),
    [items, filter]
  );

  const handleClear = () => {
    if (!items.length) return;
    if (!confirmClear) {
      setConfirmClear(true);
      return;
    }
    clearWatchlist();
    setConfirmClear(false);
  };

  return (
    <div>
      <SEO
        title={`My List - ${SITENAME}`}
        description={`Titles you saved to watch later on ${SITENAME}.`}
        name={SITENAME}
        type="website"
        /* Personal, per-visitor content — nothing useful to index. */
        noIndex
      />

      <div className="mt-8 mb-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <div className="w-px h-7 bg-projector shrink-0" />
            <h1 className="section-title text-lg sm:text-xl md:text-2xl tracking-tight">
              My List
            </h1>
            <span className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 px-2 py-1 rounded-[2px] bg-bone/5 border border-bone/10">
              {items.length} saved
            </span>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 bg-bone/5 border border-bone/10 rounded-[3px] p-1">
              {FILTERS.map((option) => {
                const isActive = filter === option.value;
                return (
                  <button
                    key={option.value}
                    onClick={() => setFilter(option.value)}
                    className={`
                      relative px-3 sm:px-4 py-1.5 rounded-[3px] text-xs sm:text-sm font-semibold
                      transition-all duration-200 whitespace-nowrap
                      ${isActive ? "text-ink" : "text-bone/50 hover:text-bone/80"}
                    `}
                  >
                    {isActive && (
                      <motion.div
                        layoutId="mylistFilterPill"
                        className="absolute inset-0 bg-projector rounded-[3px]"
                        style={{ zIndex: -1 }}
                        transition={{ type: "spring", stiffness: 500, damping: 40 }}
                      />
                    )}
                    {option.name}
                  </button>
                );
              })}
            </div>

            {items.length > 0 && (
              <button
                onClick={handleClear}
                className={`
                  px-3 py-2 rounded-[3px] border text-xs font-semibold transition-colors duration-200
                  ${confirmClear
                    ? "bg-red-500/15 border-red-500/40 text-red-300 hover:bg-red-500/20"
                    : "bg-bone/5 border-bone/10 text-bone/50 hover:bg-bone/10 hover:text-bone"
                  }
                `}
              >
                {confirmClear ? "Confirm clear" : "Clear"}
              </button>
            )}
          </div>
        </div>

        <div className="mt-4 h-px bg-gradient-to-r from-projector/20 via-bone/5 to-transparent" />
      </div>

      {visible.length === 0 ? (
        items.length === 0 ? (
          <EmptyState
            icon={PiHeartStraight}
            eyebrow="Nothing saved yet"
            title="Your list is empty"
            description="Tap the heart on any poster to keep it here for later — no account needed."
            action={
              <Link
                to="/Movies"
                className="inline-flex items-center gap-2 rounded-[3px] bg-projector px-5 py-2.5 text-xs font-bold text-ink transition-colors duration-200 hover:bg-[#F0CA98]"
              >
                Browse movies
              </Link>
            }
          />
        ) : (
          <p className="py-16 text-center text-sm text-bone/60">
            No {filter === "movie" ? "movies" : "series"} in your list yet.
          </p>
        )
      ) : (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="grid gap-x-2 sm:gap-x-3 gap-y-6 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7"
        >
          {visible.map((item, index) => (
            <motion.div
              key={`${item.media_type}:${item.tmdb_id}`}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(index * 0.03, 0.5) }}
            >
              <MovieCard movie={item} />
            </motion.div>
          ))}
        </motion.div>
      )}
    </div>
  );
}
