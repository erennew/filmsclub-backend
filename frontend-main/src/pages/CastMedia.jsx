import React, { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { UserRound } from "lucide-react";
import MovieCard from "../components/MovieCard";
import MovieCardSkeleton from "../components/MovieCardSkeleton";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";
import { BASE_URL as BASE, SITENAME } from "../config";

export default function CastMedia() {
  const { castId, castName } = useParams();

  const displayName = useMemo(() => {
    try { return decodeURIComponent(castName || "Cast"); }
    catch { return castName || "Cast"; }
  }, [castName]);

  const [media, setMedia] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [totalCount, setTotalCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    window.scrollTo(0, 0);

    axios.get(`${BASE}/api/cast/${castId}`, {
      params: { name: displayName, page: currentPage, page_size: 24 },
    })
      .then(({ data }) => {
        if (!isMounted) return;
        setMedia(Array.isArray(data.results) ? data.results : []);
        setTotalCount(Number(data.total_count || 0));
        setIsLoading(false);
      })
      .catch(() => {
        if (isMounted) { setMedia([]); setTotalCount(0); setIsLoading(false); }
      });

    return () => { isMounted = false; };
  }, [BASE, castId, displayName, currentPage]);

  return (
    <>
      <SEO
        title={`${displayName} - ${SITENAME} Cast`}
        description={`Browse all movies and series featuring ${displayName} on ${SITENAME}.`}
        name={SITENAME}
        /* Paginated person filmography with no unique editorial content —
           indexable would just compete with the title pages it links to. */
        noIndex
      />

      <div>
        {/* Cast Header */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="flex items-center gap-4 mb-8 mt-4"
        >
          {/* Avatar */}
          <div className="flex-shrink-0 w-16 h-16 rounded-[4px] bg-gradient-to-br from-projector/20 to-projector-deep/10 border border-projector/25 flex items-center justify-center">
            <UserRound className="text-projector w-8 h-8" />
          </div>
          <div className="min-w-0">
            <span className="eyebrow mb-1.5">Call sheet</span>
            <h1 className="section-title text-2xl sm:text-3xl tracking-tight truncate">
              {displayName}
            </h1>
            {!isLoading && totalCount > 0 && (
              <p className="timecode text-[11px] text-bone/60 mt-1">
                {totalCount} title{totalCount === 1 ? "" : "s"} on file
              </p>
            )}
          </div>
        </motion.div>

        {/* Divider */}
        <div className="h-px bg-gradient-to-r from-projector/20 via-bone/5 to-transparent mb-8" />

        {/* Section header */}
        <div className="flex items-center gap-3 mb-5">
          <div className="w-px h-5 bg-projector shrink-0" />
          <h2 className="section-title text-base sm:text-lg tracking-tight">Filmography</h2>
        </div>

        {/* Grid */}
        {!isLoading ? (
          media.length > 0 ? (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="grid gap-x-2 sm:gap-x-3 gap-y-6 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7"
            >
              {media.map((movie, index) => (
                <motion.div
                  key={`${movie.media_type}:${movie.tmdb_id}`}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(index * 0.03, 0.5) }}
                >
                  <MovieCard movie={movie} />
                </motion.div>
              ))}
            </motion.div>
          ) : (
            /* Empty state */
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <UserRound className="w-16 h-16 text-bone/10 mb-4" />
              <span className="eyebrow mb-3">No screenings</span>
              <p className="font-display text-xl sm:text-2xl text-bone/90">No titles on file yet</p>
              <p className="mt-2 max-w-sm text-[13px] text-bone/50">
                We don&rsquo;t have any media for {displayName} in the archive. Check back after the next programme update.
              </p>
            </div>
          )
        ) : (
          <MovieCardSkeleton count={24} />
        )}

        {/* Pagination */}
        {totalCount > 24 && (
          <Pagination
            currentPage={currentPage}
            total={totalCount}
            limit={24}
            onPageChange={(p) => { setCurrentPage(p); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
          />
        )}
      </div>
    </>
  );
}
