import React, { useState, useEffect, useRef } from "react";
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
/* See the note in MovieDetails.jsx: only one pixel of a 4x6 canvas is read, so
 * the w92 thumbnail is used instead of the full-size artwork. */
function extractPosterColor(src, onColor) {
  if (!src) return;
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = sizedImage(src, 46, 1, ["w92", "w154", "w185"]);
  img.onload = () => {
    try {
      const canvas = document.createElement("canvas");
      canvas.width = 4;
      canvas.height = 6;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, 4, 6);
      const [r, g, b] = ctx.getImageData(2, 4, 1, 1).data;
      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      if (max > 40 && max - min > 18) {
        onColor(`${r},${g},${b}`);
      } else {
        onColor("233,184,114");
      }
    } catch {
      onColor("233,184,114");
    }
  };
  img.onerror = () => onColor("233,184,114");
}

export default function TvDetails() {
  const { seriesID } = useParams();

  const [seriesDetail, setSeriesDetail] = useState({});
  const [similarSeries, setSimilarSeries] = useState([]);
  const [isDetailsLoading, setDetailsIsLoading] = useState(true);
  const [isSimilarLoading, setIsSimilarLoading] = useState(true);
  const [episodeNumber, setEpisodeNumber] = useState();
  const [seasonNumber, setSeasonNumber] = useState(1);
  const [isEpisodesLoading, setIsEpisodesLoading] = useState(true);
  const [episodes, setEpisodes] = useState([]);
  const [detailsError, setDetailsError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const online = useOnlineStatus();

  // ── Ambient color state ───────────────────────────────────────
  const [ambientRgb, setAmbientRgb] = useState("233,184,114");
  const prevSeriesID = useRef(null);

  // Fetch Series Details
  useEffect(() => {
    setDetailsIsLoading(true);
    setDetailsError(false);
    setAmbientRgb("233,184,114");
    window.scrollTo(0, 0);

    axios.get(`${BASE}/api/id/${seriesID}`)
      .then(({ data }) => {
        const sortedSeasons = (data.seasons || []).sort(
          (a, b) => a.season_number - b.season_number
        );
        setSeriesDetail({ ...data, seasons: sortedSeasons });
        if (sortedSeasons.length > 0) {
          setSeasonNumber(sortedSeasons[0].season_number);
        }
        setDetailsIsLoading(false);
        trackVisit({ ...data, seasons: sortedSeasons, media_type: "tv" });

        // Extract ambient color from poster
        if (data.poster && seriesID !== prevSeriesID.current) {
          prevSeriesID.current = seriesID;
          extractPosterColor(data.poster, setAmbientRgb);
        }
      })
      .catch(() => {
        setDetailsError(true);
        setDetailsIsLoading(false);
      });
  }, [seriesID, BASE, reloadToken]);

  // Track view
  useEffect(() => {
    if (!seriesID) return;
    fetch(`${BASE}/api/track-view?tmdb_id=${seriesID}&media_type=tv`, {
      method: "POST",
      keepalive: true,
    }).catch(() => {});
  }, [seriesID, BASE]);

  // Fetch Similar Series
  useEffect(() => {
    setIsSimilarLoading(true);
    axios.get(`${BASE}/api/similar/`, {
      params: { tmdb_id: seriesID, media_type: "tvshow", page_size: 18 },
    })
      .then(({ data }) => {
        setSimilarSeries(data.similar_media || []);
        setIsSimilarLoading(false);
      })
      .catch(() => setIsSimilarLoading(false));
  }, [seriesID, BASE]);

  // Episodes for the selected season.
  //
  // This used to issue a second GET /api/id/:id?season_number=N on every season
  // change. That endpoint returns the same TV document we already hold and then
  // plucks one season out of it, so the round-trip was pure waste — and it
  // refetched the whole show (including the cast fallback) each time. The
  // details response already carries seasons[].episodes[], so derive locally.
  useEffect(() => {
    if (seasonNumber === undefined || seasonNumber === null) return;
    const season = (seriesDetail.seasons || []).find(
      (s) => String(s.season_number) === String(seasonNumber)
    );
    setEpisodes(season?.episodes || []);
    setIsEpisodesLoading(false);
  }, [seasonNumber, seriesDetail.seasons]);

  const seriesTitle = seriesDetail?.title || "";
  const seriesYear  = seriesDetail?.release_year || "";

  return (
    // ── Ambient backdrop wrapper ────────────────────────────────
    <div className="relative min-h-screen">
      {/* Top ambient glow */}
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

      {/* Side ambient streaks (desktop only) */}
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

      {/* ── Page Content ── */}
      <div className="relative z-10">
        <SEO
          title={seriesTitle ? `${seriesTitle}${seriesYear ? ` (${seriesYear})` : ""} - ${SITENAME}` : SITENAME}
          description={seriesDetail?.description
            ? `Watch ${seriesTitle} online on ${SITENAME}. ${seriesDetail.description.slice(0, 150)}...`
            : `Stream ${seriesTitle || "TV series"} in HD quality on ${SITENAME}.`}
          name={SITENAME}
          type="video.tv_show"
          keywords={`${seriesTitle}, watch ${seriesTitle} online, stream ${seriesTitle}, ${SITENAME}`}
          image={seriesDetail?.backdrop || seriesDetail?.poster}
          imageAlt={seriesTitle ? `${seriesTitle} poster` : undefined}
          /* TVSeries rich result. */
          jsonLd={
            seriesTitle
              ? {
                  "@context": "https://schema.org",
                  "@type": "TVSeries",
                  name: seriesTitle,
                  ...(seriesDetail?.description
                    ? { description: String(seriesDetail.description).slice(0, 500) }
                    : {}),
                  ...(Number(seriesDetail?.rating) > 0
                    ? {
                        aggregateRating: {
                          "@type": "AggregateRating",
                          ratingValue: Number(seriesDetail.rating),
                          bestRating: 10,
                          worstRating: 0,
                          ratingCount: Math.max(
                            1,
                            Number(seriesDetail?.rating_count) || 1
                          ),
                        },
                      }
                    : {}),
                  ...(seriesYear ? { datePublished: String(seriesYear) } : {}),
                  ...(seriesDetail?.backdrop || seriesDetail?.poster
                    ? { image: seriesDetail?.backdrop || seriesDetail?.poster }
                    : {}),
                  ...(Number(seriesDetail?.total_seasons) > 0
                    ? {
                        numberOfSeasons: Number(seriesDetail.total_seasons),
                        ...(Number(seriesDetail?.total_episodes) > 0
                          ? { numberOfEpisodes: Number(seriesDetail.total_episodes) }
                          : {}),
                      }
                    : {}),
                  ...(Array.isArray(seriesDetail?.genres) && seriesDetail.genres.length
                    ? { genre: seriesDetail.genres.map((g) => String(g)) }
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
                ? "This series didn't make it through the projector. Try loading it again."
                : "This series isn't cached yet. Reconnect to load the episode guide."
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
              movieData={seriesDetail}
              isMovieDataLoading={isDetailsLoading}
              detailType="series"
              seasonNumber={seasonNumber}
              episodeNumber={episodeNumber}
              setEpisodeNumber={setEpisodeNumber}
              setSeasonNumber={setSeasonNumber}
              isEpisodesLoading={isEpisodesLoading}
              episodes={episodes}
              setEpisodes={setEpisodes}
            />

            <Similars
              movieData={similarSeries}
              isMovieDataLoading={isSimilarLoading}
              sectionTitle="You May Also Like"
              currentTmdbId={seriesID}
              seeMoreButtonLink={`/similarSeries/${seriesID}`}
            />
          </>
        )}
      </div>
    </div>
  );
}
