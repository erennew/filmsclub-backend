// src/pages/TopRatedSeries.jsx
import React from "react";
import MediaListPage from "../components/MediaListPage";

export default function TopRatedSeries() {
  return (
    <MediaListPage
      path="/api/tvshows"
      params={{ sort_by: "rating:desc" }}
      pick={(data) => data.tv_shows || []}
      totalOf={(data) => data.total_count ?? 0}
      sectionTitle="Top Rated Series"
      dataType="series"
      description="Discover the highest-rated TV series. Watch top-rated shows in HD quality."
      keywords="top rated series, best tv shows, highest rated series, watch series online"
    />
  );
}
