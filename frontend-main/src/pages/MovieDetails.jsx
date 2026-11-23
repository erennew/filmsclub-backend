import React, { useState, useEffect, useRef, useCallback } from "react";
import axios from "axios";
import { useParams } from "react-router-dom";
import "react-toastify/dist/ReactToastify.css";
import { BASE_URL as BASE, SITENAME } from "../config";
import { trackVisit } from "../utils/watchHistory";
import MoviesAndSeriesDetailsSections from "../components/MoviesAndSeriesDetailsSections";
import Similars from "../components/Similars";
import SEO from "../components/SEO";
import EmptyState, { RetryButton } from "../components/EmptyState";
import useOnlineStatus from "../hooks/useOnlineStatus";
import { sizedImage } from "../utils/imageUrl";
import { FiFilm, FiWifiOff } from "react-icons/fi";

// ── Palette extraction from poster ───────────────────────────────
/*
 * Only a single pixel is read, from a 4x6 canvas, but this used to hand the raw
 * poster URL to `new Image()`. That pulled the full-size artwork — frequently
 * `image.tmdb.org/t/p/original`, i.e. 1-1.5 MB — purely to sample one colour,
 * and did it eagerly on mount. The tiny thumbnail (w92, ~3 KB) yields the same
 * pixel after the same 4x6 downscale, so the ambient colour is unchanged while
 * the download disappears. This colour is decorative, so a failure is silent.
 */
function extractPosterColor(src, onColor) {
  if (!src) return;
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = sizedImage(src, 46, 1, ["w92", "w154", "w185"]);
  img.onload = () => {
    try {
      const canvas = document.createElement("canvas");
      // Sample a tiny version — fastest possible read
      canvas.width = 4;
      canvas.height = 6;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, 4, 6);
      // Use center-bottom pixel for the dominant "scene" color
      const [r, g, b] = ctx.getImageData(2, 4, 1, 1).data;
      // Only apply if it's not too dark or near-greyscale
      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      if (max > 40 && max - min > 18) {
        onColor(`${r},${g},${b}`);
      } else {
        onColor("233,184,114"); // fallback to brand blue
      }
    } catch {
      onColor("233,184,114");
    }
  };
  img.onerror = () => onColor("233,184,114");
}

export default function MovieDetails() {
  const { movieID } = useParams();

  const [movieDetail, setMovieDetail] = useState({});
  const [similarMovies, setSimilarMovies] = useState([]);
  const [isDetailsLoading, setDetailsIsLoading] = useState(true);
  const [isSimilarLoading, setIsSimilarLoading] = useState(true);
  const [detailsError, setDetailsError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const online = useOnlineStatus();

  // ── Ambient color state ───────────────────────────────────────
  const [ambientRgb, setAmbientRgb] = useState("233,184,114");
  const prevMovieID = useRef(null);

  // Fetch Movie Details
  useEffect(() => {
    setDetailsIsLoading(true);
    setDetailsError(false);
    setAmbientRgb("233,184,114"); // reset while loading
    window.scrollTo(0, 0);

    axios.get(`${BASE}/api/id/${movieID}`)
      .then(({ data }) => {
        setMovieDetail(data);
        setDetailsIsLoading(false);
        trackVisit({ ...data, media_type: "movie" });

        // Extract ambient color from the poster
        if (data.poster && movieID !== prevMovieID.current) {
          prevMovieID.current = movieID;
          extractPosterColor(data.poster, setAmbientRgb);
        }
      })
      .catch(() => {
        setDetailsError(true);
        setDetailsIsLoading(false);
      });
  }, [movieID, BASE, reloadToken]);

  // Track view (fire-and-forget)
  useEffect(() => {
    if (!movieID) return;
    fetch(`${BASE}/api/track-view?tmdb_id=${movieID}&media_type=movie`, {
      method: "POST",
      keepalive: true,
    }).catch(() => {});
  }, [movieID, BASE]);

  // Fetch Similar Movies
  useEffect(() => {
    setIsSimilarLoading(true);
    axios.get(`${BASE}/api/similar/`, {
      params: { tmdb_id: movieID, media_type: "movie", page_size: 18 },
    })
      .then(({ data }) => {
        setSimilarMovies(data.similar_media || []);
        setIsSimilarLoading(false);
      })
      .catch(() => setIsSimilarLoading(false));
  }, [movieID, BASE]);

  const movieTitle = movieDetail?.title || "";
  const movieYear  = movieDetail?.release_year || "";

  return (
    // ── Ambient backdrop wrapper ────────────────────────────────
    <div className="relative min-h-screen">
      {/* Animated ambient glow — pulses with the movie's dominant color */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 z-0 transition-all duration-[1200ms] ease-out"
        style={{
          background: `
            radial-gradient(ellipse 80% 55% at 50% -10%,
              rgba(${ambientRgb}, 0.22) 0%,
              rgba(${ambientRgb}, 0.08) 45%,
              transparent 75%
            )
          `,
        }}
      />

      {/* ── Side ambient streaks (desktop only) ── */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 z-0 hidden md:block transition-all duration-[1200ms] ease-out"
        style={{
          background: `
            radial-gradient(ellipse 30% 60% at -5% 40%,
              rgba(${ambientRgb}, 0.10) 0%,
              transparent 70%
            ),
            radial-gradient(ellipse 30% 60% at 105% 40%,
              rgba(${ambientRgb}, 0.08) 0%,
              transparent 70%
            )
          `,
        }}
      />

      {/* ── Page Content (sits above the glow) ── */}
      <div className="relative z-10">
        <SEO
          title={movieTitle ? `${movieTitle}${movieYear ? ` (${movieYear})` : ""} - ${SITENAME}` : SITENAME}
          description={movieDetail?.description
            ? `Watch ${movieTitle} online on ${SITENAME}. ${movieDetail.description.slice(0, 150)}...`
            : `Stream ${movieTitle || "movies"} in HD quality on ${SITENAME}.`}
          name={SITENAME}
          type="video.movie"
          keywords={`${movieTitle}, watch ${movieTitle} online, stream ${movieTitle}, ${SITENAME}`}
          image={movieDetail?.backdrop || movieDetail?.poster}
          imageAlt={movieTitle ? `${movieTitle} poster` : undefined}
          /* Movie rich result: lets search engines show a proper film card
             with rating, year and runtime instead of a bare blue link. */
          jsonLd={
            movieTitle
              ? {
                  "@context": "https://schema.org",
                  "@type": "Movie",
                  name: movieTitle,
                  ...(movieDetail?.description
                    ? { description: String(movieDetail.description).slice(0, 500) }
                    : {}),
                  ...(Number(movieDetail?.rating) > 0
                    ? {
                        aggregateRating: {
                          "@type": "AggregateRating",
                          ratingValue: Number(movieDetail.rating),
                          bestRating: 10,
                          worstRating: 0,
                          ratingCount: Math.max(1, Number(movieDetail?.rating_count) || 1),
                        },
                      }
                    : {}),
                  ...(movieYear ? { datePublished: String(movieYear) } : {}),
                  ...(movieDetail?.backdrop || movieDetail?.poster
                    ? { image: movieDetail?.backdrop || movieDetail?.poster }
                    : {}),
                  ...(Array.isArray(movieDetail?.genres) && movieDetail.genres.length
                    ? { genre: movieDetail.genres.map((g) => String(g)) }
                    : {}),
                }
              : undefined
          }
        />

        {detailsError ? (
          <EmptyState
            icon={online ? FiFilm : FiWifiOff}
            eyebrow={online ? "Projection interrupted" : "Signal lost"}
            title={online ? "We lost the feed" : "You're offline"}
            description={
              online
                ? "This title didn't make it through the projector. Try loading it again."
                : "This title isn't cached yet. Reconnect to load the film details."
            }
            action={
              <RetryButton
                onClick={() => setReloadToken((token) => token + 1)}
                label="Retry screening"
              />
            }
          />
        ) : (
          <>
            <MoviesAndSeriesDetailsSections
              movieData={movieDetail}
              isMovieDataLoading={isDetailsLoading}
              detailType="movie"
            />

            <Similars
              movieData={similarMovies}
              isMovieDataLoading={isSimilarLoading}
              sectionTitle="You May Also Like"
              currentTmdbId={movieID}
              seeMoreButtonLink={`/similarMov/${movieID}`}
            />
          </>
        )}
      </div>
    </div>
  );
}
