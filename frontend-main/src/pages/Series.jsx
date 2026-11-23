// src/pages/Series.jsx
import React from "react";
import MediaListPage from "../components/MediaListPage";

export default function Series() {
  return (
    <MediaListPage
      path="/api/tvshows"
      pick={(data) => data.tv_shows || []}
      totalOf={(data) => data.total_count ?? 0}
      sectionTitle="Browse Series"
      dataType="series"
      withSort
      description="Watch the latest HD TV series. Browse thousands of shows sorted by newest, top rated, and more."
      keywords="watch series online, HD series, stream TV shows free, latest series"
    />
  );
}
