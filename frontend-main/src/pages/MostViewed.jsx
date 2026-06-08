// src/pages/MostViewed.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import SEO from "../components/SEO";

export default function MostViewed() {
  const [items, setItems] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    setIsLoading(true);
    window.scrollTo(0, 0);

    axios
      .get(`${BASE}/api/most-viewed`, {
        params: { limit: 50 },
      })
      .then((response) => {
        const movies = response.data.movies || [];
        const tvShows = response.data.tv_shows || [];
        const combined = [...movies, ...tvShows].sort(
          (a, b) => (b.view_count || 0) - (a.view_count || 0)
        );
        setItems(combined);
        setIsLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching most viewed:", error);
        setIsLoading(false);
      });
  }, [BASE]);

  return (
    <div>
      <SEO
        title={`Most Viewed - ${SITENAME}`}
        description={`Discover the most viewed movies and TV shows on ${SITENAME}. Watch trending content in HD quality.`}
        name={SITENAME}
        type="text/html"
        keywords="most viewed, trending, popular movies, popular series, watch online"
        link={`https://${SITENAME}.com`}
      />
      <MoviesAndSeriesSections
        movieData={items}
        isMovieDataLoading={isLoading}
        dataType="mostViewed"
        sectionTitle="Most Viewed"
      />
    </div>
  );
}
