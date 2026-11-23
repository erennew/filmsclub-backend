// src/components/MediaListPage.jsx
import React, { useState } from "react";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "./MoviesAndSeriesSections";
import Pagination from "./Pagination";
import SEO from "./SEO";
import EmptyState, { RetryButton } from "./EmptyState";
import useMediaList from "../hooks/useMediaList";

/**
 * MediaListPage — the shared browse-page shell.
 *
 * Movies / Series / Anime / K-Drama / Top-Rated / Most-Viewed were eight
 * near-identical files that each hand-rolled the same fetch effect and each
 * swallowed the error with `.catch(() => setIsLoading(false))` — so a 500
 * looked exactly like an empty catalogue and there was no retry anywhere.
 *
 * One page now owns: fetch + abort, error/retry, empty state, SEO, pagination.
 *
 * Props:
 *   path          API path (e.g. "/api/movies")
 *   params        object merged into the query string (page is handled here)
 *   pick(data)    pull the item array out of the response
 *   totalOf(data) pull the total_count out of the response
 *   sectionTitle  heading + document title
 *   dataType      "movies" | "series" | "anime" | "kdrama" — controls the card kind
 *   description   SEO description
 *   keywords      SEO keywords
 *   withSort      show the sort selector (movies/series only)
 */
export default function MediaListPage({
  path,
  params = {},
  pick,
  totalOf,
  sectionTitle,
  dataType,
  description,
  keywords,
  withSort = false,
}) {
  const [currentPage, setCurrentPage] = useState(1);
  const [sort, setSort] = useState(withSort ? "updated_on" : null);
  const [sortVal, setSortVal] = useState(withSort ? "updated_on" : null);

  const merged = {
    ...params,
    page: currentPage,
    page_size: 20,
    ...(sort ? { sort_by: `${sort}:desc` } : {}),
  };

  const { items, total, isLoading, error, reload } = useMediaList({
    base: BASE,
    path,
    params: merged,
    pick,
    totalOf,
  });

  return (
    <div>
      <SEO
        title={`${sectionTitle} - ${SITENAME}`}
        description={description}
        name={SITENAME}
        type="website"
        keywords={keywords}
      />

      {error ? (
        <EmptyState
          eyebrow="Projection interrupted"
          title={`Couldn't load ${sectionTitle.toLowerCase()}`}
          description="The library is unreachable right now. Your connection may be offline, or the server is having a moment."
          action={<RetryButton onClick={reload} />}
        />
      ) : (
        <MoviesAndSeriesSections
          movieData={items}
          isMovieDataLoading={isLoading}
          dataType={dataType}
          sectionTitle={sectionTitle}
          setMovieFilter={withSort ? setSort : undefined}
          movieFilterVal={sortVal}
          setMovieFilterVal={withSort ? setSortVal : undefined}
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
