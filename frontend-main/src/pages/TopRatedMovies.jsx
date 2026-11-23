// src/pages/TopRatedMovies.jsx
import React from "react";
import MediaListPage from "../components/MediaListPage";

export default function TopRatedMovies() {
  return (
    <MediaListPage
      path="/api/movies"
      params={{ sort_by: "rating:desc" }}
      pick={(data) => data.movies || []}
      totalOf={(data) => data.total_count ?? 0}
      sectionTitle="Top Rated Movies"
      dataType="movies"
      description="Discover the highest-rated movies. Watch top-rated films in HD quality."
      keywords="top rated movies, best movies, highest rated films, watch movies online"
    />
  );
}
