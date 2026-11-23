// src/pages/Movies.jsx
import React, { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";
import EmptyState, { RetryButton } from "../components/EmptyState";
import useMediaList from "../hooks/useMediaList";

export default function Movies() {
  const [searchParams] = useSearchParams();
  const genreParam = searchParams.get("genre");

  const [currentPage, setCurrentPage] = useState(1);
  const [movieFilter, setMovieFilter] = useState("updated_on");
  const [movieFilterVal, setMovieFilterVal] = useState("updated_on");

  const params = { sort_by: `${movieFilter}:desc`, page: currentPage, page_size: 20 };
  if (genreParam) params.genre = genreParam;

  const { items, total, isLoading, error, reload } = useMediaList({
    base: BASE,
    path: "/api/movies",
    params,
    pick: (data) => data.movies || [],
    totalOf: (data) => data.total_count ?? 0,
  });

  const sectionTitle = genreParam ? `${genreParam} Movies` : "Browse Movies";

  return (
    <div>
      <SEO
        title={`${sectionTitle} - ${SITENAME}`}
        description={`Watch the latest HD movies on ${SITENAME}. Browse thousands of films sorted by newest, top rated, and more.`}
        name={SITENAME}
        type="website"
        keywords="watch movies online, HD movies, stream movies free, latest movies"
      />

      {error ? (
        <EmptyState
          eyebrow="Projection interrupted"
          title="Couldn't load movies"
          description="The library is unreachable right now. Your connection may be offline, or the server is having a moment."
          action={<RetryButton onClick={reload} />}
        />
      ) : (
        <MoviesAndSeriesSections
          movieData={items}
          isMovieDataLoading={isLoading}
          dataType="movies"
          sectionTitle={sectionTitle}
          setMovieFilter={setMovieFilter}
          movieFilterVal={movieFilterVal}
          setMovieFilterVal={setMovieFilterVal}
        />
      )}

      {!error && (
        <Pagination
          currentPage={currentPage}
          total={total}
          onPageChange={(p) => {
            setCurrentPage(p);
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
          limit={20}
        />
      )}
    </div>
  );
}
