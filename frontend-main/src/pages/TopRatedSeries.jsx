// src/pages/TopRatedSeries.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

export default function TopRatedSeries() {
  const [series, setSeries] = useState([]);
  const [isSeriesDataLoading, setIsSeriesDataLoading] = useState(true);
  const [seriesDataForPageCount, setSeriesDataForPageCount] = useState("");
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    setIsSeriesDataLoading(true);
    window.scrollTo(0, 0);

    axios
      .get(`${BASE}/api/tvshows`, {
        params: {
          sort_by: "rating:desc",
          page: currentPage,
          page_size: 20,
        },
      })
      .then((response) => {
        setSeries(response.data.tv_shows);
        setSeriesDataForPageCount(response.data.total_count);
        setIsSeriesDataLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching top rated series:", error);
        setIsSeriesDataLoading(false);
      });
  }, [currentPage, BASE]);

  return (
    <div>
      <SEO
        title={`Top Rated Series - ${SITENAME}`}
        description={`Discover the highest-rated TV series on ${SITENAME}. Watch top-rated shows in HD quality.`}
        name={SITENAME}
        type="text/html"
        keywords="top rated series, best tv shows, highest rated series, watch series online"
        link={`https://${SITENAME}.com`}
      />
      <MoviesAndSeriesSections
        movieData={series}
        isMovieDataLoading={isSeriesDataLoading}
        dataType="series"
        sectionTitle="Top Rated Series"
      />
      <Pagination
        currentPage={currentPage}
        total={seriesDataForPageCount}
        onPageChange={(p) => {
          setCurrentPage(p);
        }}
        limit={20}
      />
    </div>
  );
}
