// src/pages/KDrama.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

export default function KDrama() {
  const [items, setItems] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [totalCount, setTotalCount] = useState("");
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    setIsLoading(true);
    window.scrollTo(0, 0);

    axios
      .get(`${BASE}/api/k-drama`, {
        params: {
          page: currentPage,
          page_size: 20,
        },
      })
      .then((response) => {
        setItems(response.data.tv_shows || []);
        setTotalCount(response.data.total_count || 0);
        setIsLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching K-Drama:", error);
        setIsLoading(false);
      });
  }, [currentPage, BASE]);

  return (
    <div>
      <SEO
        title={`K-Drama - ${SITENAME}`}
        description={`Watch the best Korean dramas on ${SITENAME}. Stream K-Drama in HD quality.`}
        name={SITENAME}
        type="text/html"
        keywords="k-drama, korean drama, kdrama, watch kdrama, korean series"
        link={`https://${SITENAME}.com`}
      />
      <MoviesAndSeriesSections
        movieData={items}
        isMovieDataLoading={isLoading}
        dataType="kdrama"
        sectionTitle="K-Drama"
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
