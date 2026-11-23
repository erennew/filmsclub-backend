// src/pages/Anime.jsx
import React from "react";
import MediaListPage from "../components/MediaListPage";

export default function Anime() {
  return (
    <MediaListPage
      path="/api/anime"
      pick={(data) => data.results || []}
      totalOf={(data) => data.total_count ?? 0}
      sectionTitle="Anime"
      dataType="anime"
      description="Watch the best anime movies and series. Stream anime in HD quality."
      keywords="anime, watch anime, anime movies, anime series, stream anime"
    />
  );
}
