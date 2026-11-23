// src/pages/KDrama.jsx
import React from "react";
import MediaListPage from "../components/MediaListPage";

export default function KDrama() {
  return (
    <MediaListPage
      path="/api/k-drama"
      pick={(data) => data.tv_shows || []}
      totalOf={(data) => data.total_count ?? 0}
      sectionTitle="K-Drama"
      dataType="kdrama"
      description="Watch the best Korean dramas. Stream K-Drama in HD quality."
      keywords="k-drama, korean drama, kdrama, watch kdrama, korean series"
    />
  );
}
