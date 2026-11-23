// src/pages/SimilarMov.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { motion } from "framer-motion";
import { useParams } from "react-router-dom";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

export default function SimilarMov() {
  const [movies, setMovies] = useState([]);
  const [isMoviesDataLoading, setIsMoviesDataLoading] = useState(true);
  const [moviesDataForPageCount, setMoviesDataForPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [title, setTitle] = useState("");
  const { movieID } = useParams();

  useEffect(() => {
    setIsMoviesDataLoading(true);
    window.scrollTo(0, 0);

    axios.get(`${BASE}/api/similar/`, {
      params: { tmdb_id: movieID, media_type: "movie", page_size: 20, page: currentPage },
    })
      .then(({ data }) => {
        setMovies(data.similar_media || []);
        setMoviesDataForPageCount(data.total_count || 0);
        setIsMoviesDataLoading(false);
      })
      .catch(() => setIsMoviesDataLoading(false));
  }, [currentPage, movieID, BASE]);

  useEffect(() => {
    axios.get(`${BASE}/api/id/${movieID}`)
      .then(({ data }) => setTitle(data.title || ""))
      .catch(() => {});
  }, [movieID, BASE]);

  return (
    <div>
      <SEO
        title={title ? `Similar to ${title} - ${SITENAME}` : `Similar Movies - ${SITENAME}`}
        description={`Discover movies similar to ${title || "this film"} on ${SITENAME}.`}
        name={SITENAME}
        type="website"
        keywords={`similar movies, ${title}, watch online, stream movies`}
      />

      <MoviesAndSeriesSections
        movieData={movies}
        isMovieDataLoading={isMoviesDataLoading}
        dataType="similarMovies"
        sectionTitle={title ? `More like "${title}"` : "Similar Movies"}
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
