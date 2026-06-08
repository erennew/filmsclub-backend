// src/pages/Anime.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

export default function Anime() {
  const [items, setItems] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [totalCount, setTotalCount] = useState("");
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    setIsLoading(true);
    window.scrollTo(0, 0);

    axios
      .get(`${BASE}/api/anime`, {
        params: {
          page: currentPage,
          page_size: 20,
        },
      })
      .then((response) => {
        setItems(response.data.results || []);
        setTotalCount(response.data.total_count || 0);
        setIsLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching anime:", error);
        setIsLoading(false);
      });
  }, [currentPage, BASE]);

  return (
    <div>
      <SEO
        title={`Anime - ${SITENAME}`}
        description={`Watch the best anime movies and series on ${SITENAME}. Stream anime in HD quality.`}
        name={SITENAME}
        type="text/html"
        keywords="anime, watch anime, anime movies, anime series, stream anime"
        link={`https://${SITENAME}.com`}
      />
      <MoviesAndSeriesSections
        movieData={items}
        isMovieDataLoading={isLoading}
        dataType="anime"
        sectionTitle="Anime"
      />
      <Pagination
        currentPage={currentPage}
        total={totalCount}
        onPageChange={(p) => {
          setCurrentPage(p);
        }}
        limit={20}
      />
    </div>
  );
}
