// src/pages/Home.jsx
import React, { useState, useEffect } from "react";
import axios from "axios";
import { BASE_URL as BASE, SITENAME } from "../config";
import HeroSlider from "../components/HomeHero";
import HomeSections from "../components/HomeSections";
import TopTenToday from "../components/TopTenToday";
import SEO from "../components/SEO";
import { ToastContainer } from "react-toastify";
import "react-toastify/dist/ReactToastify.css";

export default function Home() {
  // States
  const [heroPopularMovies, setHeroPopularMovies] = useState([]);
  const [trendingMovies, setTrendingMovies] = useState([]);
  const [trendingTv, setTrendingTv] = useState([]);
  const [topTenData, setTopTenData] = useState([]);
  const [topRatedMovies, setTopRatedMovies] = useState([]);
  const [topRatedSeries, setTopRatedSeries] = useState([]);
  const [mostViewed, setMostViewed] = useState([]);
  const [anime, setAnime] = useState([]);
  const [kdrama, setKdrama] = useState([]);
  const [isHeroLoading, setIsHeroLoading] = useState(true);
  const [isTrendingMoviesLoading, setIsTrendingMoviesLoading] = useState(true);
  const [isTrendingTvLoading, setIsTrendingTvLoading] = useState(true);
  const [isTopTenLoading, setIsTopTenLoading] = useState(true);
  const [isTopRatedMoviesLoading, setIsTopRatedMoviesLoading] = useState(true);
  const [isTopRatedSeriesLoading, setIsTopRatedSeriesLoading] = useState(true);
  const [isMostViewedLoading, setIsMostViewedLoading] = useState(true);
  const [isAnimeLoading, setIsAnimeLoading] = useState(true);
  const [isKdramaLoading, setIsKdramaLoading] = useState(true);

  useEffect(() => {
    setIsHeroLoading(true);
    window.scrollTo(0, 0);
    axios
      .get(`${BASE}/api/movies`, {
        params: {
          sort_by: "release_year:desc",
          page: 1,
          page_size: 10,
        },
      })
      .then((response) => {
        setHeroPopularMovies(response.data.movies);
        setIsHeroLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching hero popular movies:", error);
        setIsHeroLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsTrendingMoviesLoading(true);
    axios
      .get(`${BASE}/api/movies`, {
        params: {
          sort_by: "updated_on:desc",
          page: 1,
          page_size: 20,
        },
      })
      .then((response) => {
        setTrendingMovies(response.data.movies);
        setIsTrendingMoviesLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching trending movies:", error);
        setIsTrendingMoviesLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsTrendingTvLoading(true);
    axios
      .get(`${BASE}/api/tvshows`, {
        params: {
          sort_by: "updated_on:desc",
          page: 1,
          page_size: 20,
        },
      })
      .then((response) => {
        setTrendingTv(response.data.tv_shows);
        setIsTrendingTvLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching trending TV shows:", error);
        setIsTrendingTvLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsTopTenLoading(true);
    axios
      .get(`${BASE}/api/trending/today`, { params: { limit: 10 } })
      .then((response) => {
        const movies = response.data.movies || [];
        const tvShows = response.data.tv_shows || [];
        const combined = [...movies, ...tvShows].sort(
          (a, b) => (b.view_count || 0) - (a.view_count || 0)
        );
        setTopTenData(combined.slice(0, 10));
        setIsTopTenLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching trending today:", error);
        setIsTopTenLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsTopRatedMoviesLoading(true);
    axios
      .get(`${BASE}/api/movies`, {
        params: {
          sort_by: "rating:desc",
          page: 1,
          page_size: 20,
        },
      })
      .then((response) => {
        setTopRatedMovies(response.data.movies);
        setIsTopRatedMoviesLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching top rated movies:", error);
        setIsTopRatedMoviesLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsTopRatedSeriesLoading(true);
    axios
      .get(`${BASE}/api/tvshows`, {
        params: {
          sort_by: "rating:desc",
          page: 1,
          page_size: 20,
        },
      })
      .then((response) => {
        setTopRatedSeries(response.data.tv_shows);
        setIsTopRatedSeriesLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching top rated series:", error);
        setIsTopRatedSeriesLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsMostViewedLoading(true);
    axios
      .get(`${BASE}/api/most-viewed`, { params: { limit: 20 } })
      .then((response) => {
        const movies = response.data.movies || [];
        const tvShows = response.data.tv_shows || [];
        const combined = [...movies, ...tvShows].sort(
          (a, b) => (b.view_count || 0) - (a.view_count || 0)
        );
        setMostViewed(combined.slice(0, 20));
        setIsMostViewedLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching most viewed:", error);
        setIsMostViewedLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsAnimeLoading(true);
    axios
      .get(`${BASE}/api/anime`, { params: { page: 1, page_size: 20 } })
      .then((response) => {
        setAnime(response.data.results || []);
        setIsAnimeLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching anime:", error);
        setIsAnimeLoading(false);
      });
  }, [BASE]);

  useEffect(() => {
    setIsKdramaLoading(true);
    axios
      .get(`${BASE}/api/k-drama`, { params: { page: 1, page_size: 20 } })
      .then((response) => {
        setKdrama(response.data.tv_shows || []);
        setIsKdramaLoading(false);
      })
      .catch((error) => {
        console.error("Error fetching K-Drama:", error);
        setIsKdramaLoading(false);
      });
  }, [BASE]);

  return (
    <div>
      <ToastContainer style={{ fontSize: "0.8rem" }} />
      {/* SEO SECTION */}
      <SEO
        title={SITENAME}
        description={`Discover a world of entertainment where every show, movie, and exclusive content takes you on a journey beyond the screen. ${SITENAME} offers endless options for every mood, helping you relax, escape, and imagine more. Stream your favorites, dream big, and repeat the experience, only with ${SITENAME}.`}
        name={SITENAME}
        type="text/html"
        keywords="watch movies online, watch hd movies, watch full movies, streaming movies online, free streaming movie, watch movies free, watch hd movies online, watch series online, watch hd series free, free tv series, free movies online, tv online, tv links, tv links movies, free tv shows, watch tv shows online, watch tv shows online free, free hd movies, New Movie Releases, Top Movies of the Year, Watch Movies Online, Streaming Services, Movie Reviews, Upcoming Films, Best Movie Scenes, Classic Movies, HD Movie Streaming, Film Trailers, Action Movies, Drama Films, Comedy Movies, Sci-Fi Films, Horror Movie Picks, Family-Friendly Movies, Award-Winning Films, Movie Recommendations, Cinematic Experiences, Behind-the-Scenes, Director Spotlights, Actor Interviews, Film Festivals, Cult Classics, Top Box Office Hits, Celebrity News, Movie Soundtracks, Oscar-Winning Movies, Movie Trivia, Exclusive Film Content, Best Cinematography, Must-Watch Movies, Film Industry News, Filmmaking Tips, Top Movie Blogs, Latest Movie Gossip, Interactive Movie Quizzes, Red Carpet Moments, IMDb Ratings, Movie Fan Communities, fmovies, fmovies.to, fmovies to, fmovies is, fmovie, free movies, online movie, movie online, free movies online, watch movies online free, free hd movies, watch movies online"
        link={`https://${SITENAME}.com`}
      />

      {/* HEADER - Hero and boxoffice */}
      <div className="col-span-1 lg:col-span-2">
        <HeroSlider
          movieData={heroPopularMovies}
          isMovieDataLoading={isHeroLoading}
          dataType="heroPopularMovies"
          sliderTypePrev="slideHeroTrendingMovies-prev"
          sliderTypeNext="slideHeroTrendingMovies-next"
        />
      </div>
      {/* Top 10 Today Section */}
      <TopTenToday
        data={topTenData}
        isLoading={isTopTenLoading}
        sectionTitle="Top 10 Today"
      />
      {/* Trending Movies Section */}
      <HomeSections
        movieData={trendingMovies}
        isMovieDataLoading={isTrendingMoviesLoading}
        sectionTitle="Latest Movies"
        sectionSeeMoreButtonLink="/Movies"
        dataType="latestMovies"
      />
      {/* Trending TV SHOWS Section */}
      <HomeSections
        movieData={trendingTv}
        isMovieDataLoading={isTrendingTvLoading}
        sectionTitle="Latest Series"
        sectionSeeMoreButtonLink="/Series"
        dataType="latestTv"
      />
      {/* Top Rated Movies Section */}
      <HomeSections
        movieData={topRatedMovies}
        isMovieDataLoading={isTopRatedMoviesLoading}
        sectionTitle="Top Rated Movies"
        sectionSeeMoreButtonLink="/top-rated-movies"
        dataType="topRatedMovies"
      />
      {/* Top Rated Series Section */}
      <HomeSections
        movieData={topRatedSeries}
        isMovieDataLoading={isTopRatedSeriesLoading}
        sectionTitle="Top Rated Series"
        sectionSeeMoreButtonLink="/top-rated-series"
        dataType="topRatedSeries"
      />
      {/* Most Viewed Section */}
      <HomeSections
        movieData={mostViewed}
        isMovieDataLoading={isMostViewedLoading}
        sectionTitle="Most Viewed"
        sectionSeeMoreButtonLink="/most-viewed"
        dataType="mostViewed"
      />
      {/* Anime Section */}
      <HomeSections
        movieData={anime}
        isMovieDataLoading={isAnimeLoading}
        sectionTitle="Anime"
        sectionSeeMoreButtonLink="/anime"
        dataType="anime"
      />
      {/* K-Drama Section */}
      <HomeSections
        movieData={kdrama}
        isMovieDataLoading={isKdramaLoading}
        sectionTitle="K-Drama"
        sectionSeeMoreButtonLink="/k-drama"
        dataType="kdrama"
      />
    </div>
  );
}
