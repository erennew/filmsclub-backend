// src/pages/MostViewed.jsx
import React from "react";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import SEO from "../components/SEO";
import EmptyState, { RetryButton } from "../components/EmptyState";
import useMediaList from "../hooks/useMediaList";

export default function MostViewed() {
  const { items, isLoading, error, reload } = useMediaList({
    base: BASE,
    path: "/api/most-viewed",
    params: { limit: 50 },
    pick: (data) => {
      const movies = data.movies || [];
      const tvShows = data.tv_shows || [];
      return [...movies, ...tvShows].sort(
        (a, b) => (b.view_count || 0) - (a.view_count || 0)
      );
    },
    totalOf: (_, list) => list.length,
  });

  return (
    <div>
      <SEO
        title={`Most Viewed - ${SITENAME}`}
        description={`Discover the most viewed movies and TV shows on ${SITENAME}. Watch trending content in HD quality.`}
        name={SITENAME}
        type="website"
        keywords="most viewed, trending, popular movies, popular series, watch online"
      />

      {error ? (
        <EmptyState
          eyebrow="Projection interrupted"
          title="Couldn't load most viewed"
          description="The library is unreachable right now. Your connection may be offline, or the server is having a moment."
          action={<RetryButton onClick={reload} />}
        />
      ) : (
        <MoviesAndSeriesSections
          movieData={items}
          isMovieDataLoading={isLoading}
          dataType="mostViewed"
          sectionTitle="Most Viewed"
        />
      )}
    </div>
  );
}
