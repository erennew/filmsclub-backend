import React, { useState, useEffect } from "react";
import axios from "axios";
import { motion } from "framer-motion";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import MovieCardSkeleton from "../components/MovieCardSkeleton";
import { useParams } from "react-router-dom";
import SEO from "../components/SEO";
import EmptyState, { RetryButton } from "../components/EmptyState";
import useOnlineStatus from "../hooks/useOnlineStatus";
import { FiFilm, FiSearch, FiWifiOff } from "react-icons/fi";

export default function SearchResults() {
  const [movies, setMovies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [totalCount, setTotalCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [error, setError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const online = useOnlineStatus();
  const { searchResult } = useParams();

  useEffect(() => {
    setIsLoading(true);
    setCurrentPage(1);
    window.scrollTo(0, 0);
  }, [searchResult]);

  useEffect(() => {
    if (!searchResult) return;
    setIsLoading(true);
    setError(false);
    window.scrollTo(0, 0);

    axios.get(`${BASE}/api/search/`, {
      params: { query: searchResult, page: currentPage, page_size: 20 },
    })
      .then(({ data }) => {
        setMovies(data.results || []);
        setTotalCount(data.total_count || 0);
        setIsLoading(false);
      })
      .catch(() => {
        setError(true);
        setIsLoading(false);
      });
  }, [searchResult, currentPage, BASE, reloadToken]);

  return (
    <div>
      <SEO
        title={`"${searchResult}" - Search Results | ${SITENAME}`}
        description={`Search results for "${searchResult}" on ${SITENAME}. Find movies, series, anime and more.`}
        name={SITENAME}
        type="website"
        keywords={`${searchResult}, watch online, stream, movies, series`}
        /* Every distinct query is a thin, near-duplicate listing page; letting
           them all be indexed fills the crawl budget with junk. */
        noIndex
      />

      {/* Page Header */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="mt-2 mb-6"
      >
        <div className="flex items-center gap-3">
          <FiSearch className="text-projector text-xl shrink-0" />
          <div>
            <span className="eyebrow mb-1">Search results</span>
            <h1 className="section-title text-xl sm:text-2xl tracking-tight">
              “{searchResult}”
            </h1>
          </div>
        </div>
        {!isLoading && (
          <p className="timecode text-[11px] text-bone/60 mt-2 ml-8">
            {totalCount === 0
              ? "No results"
              : `${totalCount} result${totalCount === 1 ? "" : "s"} on file`}
          </p>
        )}
        <div className="mt-4 h-px bg-gradient-to-r from-projector/20 via-bone/5 to-transparent" />
      </motion.div>

      {/* Results */}
      {isLoading ? (
        <MovieCardSkeleton count={20} />
      ) : error ? (
        <EmptyState
          icon={online ? FiFilm : FiWifiOff}
          eyebrow={online ? "Projection interrupted" : "Signal lost"}
          title={online ? "We lost the feed" : "You're offline"}
          description={
            online
              ? "The archive didn't answer in time. Give it another reel."
              : "Search needs a connection the first time. Reconnect and try again."
          }
          action={
            <RetryButton
              onClick={() => setReloadToken((token) => token + 1)}
              label="Retry search"
            />
          }
        />
      ) : movies.length > 0 ? (
        <>
          <MoviesAndSeriesSections
            movieData={movies}
            isMovieDataLoading={false}
            dataType="searchResult"
            sectionTitle=""
          />
          <Pagination
            currentPage={currentPage}
            total={totalCount}
            onPageChange={(p) => {
              setCurrentPage(p);
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
            limit={20}
          />
        </>
      ) : (
        <EmptyState
          icon={FiSearch}
          eyebrow="No matches"
          title="Nothing in the archive"
          description={`We couldn't find anything for “${searchResult}”. Try a different title, actor, or year.`}
        />
      )}
    </div>
  );
}
