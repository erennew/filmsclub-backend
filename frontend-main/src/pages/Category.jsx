// src/pages/Category.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { BASE_URL as BASE, SITENAME } from "../config";
import MoviesAndSeriesSections from "../components/MoviesAndSeriesSections";
import Pagination from "../components/Pagination";
import SEO from "../components/SEO";

export default function Category({ genre, mediaType, title }) {
  const [items, setItems] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [totalCount, setTotalCount] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [sortFilter, setSortFilter] = useState("updated_on");
  const [sortFilterVal, setSortFilterVal] = useState("updated_on");
  const [genreFilter, setGenreFilter] = useState("");

  const apiEndpoint = mediaType === "movies" ? "api/movies" : "api/tvshows";
  const dataKey = mediaType === "movies" ? "movies" : "tv_shows";
  const sectionDataType = mediaType === "movies" ? "movies" : "series";

  useEffect(() => {
    setIsLoading(true);
    window.scrollTo(0, 0);

    const params = {
      sort_by: `${sortFilter}:desc`,
      page: currentPage,
      page_size: 20,
    };

    const activeGenre = genreFilter || genre;
    if (activeGenre) {
      params.genre = activeGenre;
    }

    axios
      .get(`${BASE}/${apiEndpoint}`, { params })
      .then((response) => {
        setItems(response.data[dataKey]);
        setTotalCount(response.data.total_count);
        setIsLoading(false);
      })
      .catch((error) => {
        console.error(`Error fetching ${title}:`, error);
        setIsLoading(false);
      });
  }, [sortFilter, currentPage, genreFilter, genre, mediaType, BASE]);

  return (
    <div>
      <SEO
        title={`${title} - ${SITENAME}`}
        description={`Browse the best ${title} content on ${SITENAME}. Stream your favorites in HD quality.`}
        name={SITENAME}
        type="text/html"
        keywords={`${title}, watch ${title} online, streaming ${title}, free ${title}, hd ${title}`}
        link={`https://${SITENAME}.com`}
      />
      <MoviesAndSeriesSections
        movieData={items}
        isMovieDataLoading={isLoading}
        dataType={sectionDataType}
        sectionTitle={title}
        setMovieFilter={setSortFilter}
        movieFilterVal={sortFilterVal}
        setMovieFilterVal={setSortFilterVal}
        genreFilter={genreFilter}
        setGenreFilter={setGenreFilter}
      />
      <Pagination
        currentPage={currentPage}
        total={totalCount}
        pagesNum={Math.ceil(totalCount / 20)}
        onPageChange={(p) => {
          setCurrentPage(p);
        }}
        limit={20}
      />
    </div>
  );
}
