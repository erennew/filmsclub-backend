// src/pages/SimilarSeries.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { useParams } from "react-router-dom";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

export default function SimilarSeries() {
  const [movies, setMovies] = useState([]);
  const [isMoviesDataLoading, setIsMoviesDataLoading] = useState(true);
  const [moviesDataForPageCount, setMoviesDataForPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [name, setName] = useState("");
  const { seriesID } = useParams();

  useEffect(() => {
    setIsMoviesDataLoading(true);
    window.scrollTo(0, 0);

    axios.get(`${BASE}/api/similar/`, {
      params: { tmdb_id: seriesID, media_type: "tvshow", page_size: 20, page: currentPage },
    })
      .then(({ data }) => {
        setMovies(data.similar_media || []);
        setMoviesDataForPageCount(data.total_count || 0);
        setIsMoviesDataLoading(false);
      })
      .catch(() => setIsMoviesDataLoading(false));
  }, [currentPage, seriesID, BASE]);

  useEffect(() => {
    axios.get(`${BASE}/api/id/${seriesID}`)
      .then(({ data }) => setName(data.title || ""))
      .catch(() => {});
  }, [seriesID, BASE]);

  return (
    <div>
      <SEO
        title={name ? `Similar to ${name} - ${SITENAME}` : `Similar Series - ${SITENAME}`}
        description={`Discover TV series similar to ${name || "this show"} on ${SITENAME}.`}
        name={SITENAME}
        type="website"
        keywords={`similar series, ${name}, watch online, stream series`}
      />

      <MoviesAndSeriesSections
        movieData={movies}
        isMovieDataLoading={isMoviesDataLoading}
        dataType="similarSeries"
        sectionTitle={name ? `More like "${name}"` : "Similar Series"}
      />

      <Pagination
        currentPage={currentPage}
        total={moviesDataForPageCount}
        onPageChange={(p) => {
          setCurrentPage(p);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
        limit={20}
      />
    </div>
  );
}
