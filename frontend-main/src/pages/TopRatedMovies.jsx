// src/pages/TopRatedMovies.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

export default function TopRatedMovies() {
  const [movies, setMovies] = useState([]);
  const [isMoviesDataLoading, setIsMoviesDataLoading] = useState(true);
  const [moviesDataForPageCount, setMoviesDataForPageCount] = useState("");
  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    setIsMoviesDataLoading(true);
    window.scrollTo(0, 0);

    axios
      .get(`${BASE}/api/movies`, {
        params: {
          sort_by: "rating:desc",
          page: currentPage,
          page_size: 20,
        },
      })
      .then((response) => {
        setMovies(response.data.movies);
        setMoviesDataForPageCount(response.data.total_count);
        setIsMoviesDataLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching top rated movies:", error);
        setIsMoviesDataLoading(false);
      });
  }, [currentPage, BASE]);

  return (
    <div>
      <SEO
        title={`Top Rated Movies - ${SITENAME}`}
        description={`Discover the highest-rated movies on ${SITENAME}. Watch top-rated films in HD quality.`}
        name={SITENAME}
        type="text/html"
        keywords="top rated movies, best movies, highest rated films, watch movies online"
        link={`https://${SITENAME}.com`}
      />
      <MoviesAndSeriesSections
        movieData={movies}
        isMovieDataLoading={isMoviesDataLoading}
        dataType="movies"
        sectionTitle="Top Rated Movies"
      />
      <Pagination
        currentPage={currentPage}
        total={moviesDataForPageCount}
        onPageChange={(p) => {
          setCurrentPage(p);
        }}
        limit={20}
      />
    </div>
  );
}
