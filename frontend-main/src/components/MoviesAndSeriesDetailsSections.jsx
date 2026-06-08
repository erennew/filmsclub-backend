import React, { useState, useRef } from "react";
import Watch from "./Watch";
import InlinePlayer from "./InlinePlayer";
import "react-lazy-load-image-component/src/effects/black-and-white.css";
import { LazyLoadImage } from "react-lazy-load-image-component";
import { BiPlay, BiTime } from "react-icons/bi";
import { FiCalendar } from "react-icons/fi";
import { BsListStars } from "react-icons/bs";
import { PiStarFill } from "react-icons/pi";
import { LuLanguages } from "react-icons/lu";
import { FaCloudDownloadAlt, FaPlay, FaDesktop } from "react-icons/fa";
import { PiTelegramLogo } from "react-icons/pi";
import TelegramButton from "./TelegramButtons";
import DownloadButton from "./Buttons";
import { MdOutlineHighQuality } from "react-icons/md";
import { BASE_URL as BASE, TG_USERNAME as USERNAME } from "../config";

export default function MoviesAndSeriesDetailsSections(props) {
  const [isWatchMoviePopupOpen, setIsWatchMoviePopupOpen] = useState(false);
  const [activeEpisode, setActiveEpisode] = useState(null);
  const [expandedAction, setExpandedAction] = useState({ episode: null, type: null });
  const playerWrapperRef = useRef(null);

  const handlePlayEpisode = (eps) => {
    // props.episodes may not include telegram data; look up the full episode from movieData.seasons
    const season = props.movieData.seasons?.find((s) => s.season_number === props.seasonNumber);
    const fullEpisode = season?.episodes?.find((ep) => ep.episode_number === eps.episode_number);
    setActiveEpisode(fullEpisode || eps);
    props.setEpisodeNumber(eps.episode_number);
    setTimeout(() => {
      playerWrapperRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  };

  const toggleAction = (episodeNumber, type) => {
    if (expandedAction.episode === episodeNumber && expandedAction.type === type) {
      setExpandedAction({ episode: null, type: null });
    } else {
      setExpandedAction({ episode: episodeNumber, type });
    }
  };

  const handleDownload = (q) => {
    const url = `${BASE}/dl/${q.id}/${encodeURIComponent(q.name)}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = q.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleTelegram = (tmdbId, seasonNum, quality) => {
    const url = `https://t.me/${USERNAME}?start=file_${tmdbId}_${seasonNum}_${quality}`;
    window.open(url, "_blank", "noopener noreferrer");
  };

  const handleMxPlayer = (q) => {
    const url = `${BASE}/dl/${q.id}/${encodeURIComponent(q.name)}`;
    const intentUrl = `intent:${url}#Intent;type=video/x-matroska;action=android.intent.action.VIEW;end;`;
    window.open(intentUrl, "_blank");
  };

  const handlePcPlayerM3U = (q) => {
    const isMobile = /Android|iPhone|iPad/i.test(navigator.userAgent);
    const streamUrl = `${BASE}/dl/${q.id}/${encodeURIComponent(q.name)}`;
    const safeName = q.name.replace(/[^a-zA-Z0-9]/g, "_");

    if (isMobile) {
      const a = document.createElement("a");
      a.href = streamUrl;
      a.download = q.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }

    try {
      const vlcUrl = `vlc://${encodeURIComponent(streamUrl)}`;
      window.location.href = vlcUrl;
      setTimeout(() => {
        if (document.hasFocus()) {
          const m3uContent = `#EXTM3U\n#EXTINF:-1,${safeName}\n${streamUrl}`;
          const blob = new Blob([m3uContent], { type: "audio/x-mpegurl" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `${safeName}.m3u`;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(() => URL.revokeObjectURL(url), 100);
        }
      }, 1000);
    } catch (error) {
      console.error("VLC protocol failed:", error);
      const m3uContent = `#EXTM3U\n#EXTINF:-1,${safeName}\n${streamUrl}`;
      const blob = new Blob([m3uContent], { type: "audio/x-mpegurl" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${safeName}.m3u`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 100);
    }
  };

  const handlePcPlayerMP4 = (q) => {
    const url = `${BASE}/dl/${q.id}/${encodeURIComponent(q.name)}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = q.name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const playerSources =
    activeEpisode?.telegram?.length > 0
      ? activeEpisode.telegram.map((q) => ({
          src: `${BASE}/dl/${q.id}/${q.name}`,
          type: "video/mp4",
          size: parseInt(q.quality.replace("p", ""), 10),
        }))
      : [];

  const playerPoster = activeEpisode?.episode_backdrop || props.movieData?.backdrop || "";
  const playerFileId = activeEpisode?.telegram?.[0]?.id || "";
  const playerFileName = activeEpisode?.telegram?.[0]?.name || "";

  return (
    <div className="relative mt-20 bg-btnColor/40 p-3 md:p-10 rounded-3xl">
      {!props.isMovieDataLoading ? (
        <>
          {/* ── SERIES: INLINE PLAYER ── */}
          {props.detailType === "series" && (
            <div ref={playerWrapperRef} className="w-full mb-6">
              {playerSources.length > 0 ? (
                <InlinePlayer
                  key={playerFileId || "no-episode"}
                  sources={playerSources}
                  poster={playerPoster}
                  fileId={playerFileId}
                  fileName={playerFileName}
                />
              ) : (
                <div className="aspect-video w-full relative flex items-center shrink-0 bg-btnColor rounded-2xl overflow-hidden">
                  <LazyLoadImage
                    src={props.movieData.backdrop}
                    effect="black-and-white"
                    alt={props.movieData.title}
                    className="aspect-video w-full rounded-2xl shrink-0 bg-btnColor"
                  />
                  <div className="absolute inset-0 flex items-center justify-center bg-black/30">
                    <span className="text-white/70 text-sm sm:text-base font-medium">
                      Select an episode to play
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── MOVIE: BACKDROP + METADATA GRID ── */}
          {props.detailType === "movie" && (
            <div className="grid lg:grid-cols-2 content-center items-center gap-5">
              <div
                onClick={() => setIsWatchMoviePopupOpen(true)}
                className="aspect-video w-full relative flex items-center shrink-0 bg-btnColor rounded-3xl cursor-pointer transition-all duration-300 ease-in-out hover:scale-105"
              >
                <div className="absolute z-10 top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-otherColor bg-otherColor/50 cursor-pointer rounded-full text-4xl sm:text-5xl">
                  <BiPlay />
                </div>
                <LazyLoadImage
                  src={props.movieData.backdrop}
                  effect="black-and-white"
                  alt={props.movieData.title}
                  className="aspect-video w-full rounded-3xl shrink-0 bg-btnColor"
                />
              </div>
              <div className="p-5">
                {props.movieData.genres && (
                  <div className="text-otherColor flex gap-2 flex-wrap text-sm xl:text-md">
                    {props.movieData.genres.map((genre, index) => (
                      <p key={index}>{genre}</p>
                    ))}
                  </div>
                )}
                <h1 className="text-primaryTextColor font-extrabold line-clamp-1 text-2xl xl:text-3xl">
                  {props.movieData.title}
                </h1>
                <p className="text-secondaryTextColor line-clamp-2 mt-2 text-xs xl:text-sm">
                  {props.movieData.description}
                </p>
                <div className="flex gap-2 text-primaryTextColor flex-wrap mt-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="flex items-center gap-2">
                      <BiTime className="text-secondaryTextColor text-xl xl:text-2xl" />
                      <p className="text-xs xl:text-sm">{props.movieData.runtime} min</p>
                    </div>
                    {props.movieData.release_year && (
                      <div className="flex items-center gap-2">
                        <FiCalendar className="text-secondaryTextColor text-lg xl:text-xl" />
                        <p className="text-xs xl:text-sm">{props.movieData.release_year}</p>
                      </div>
                    )}
                    {props.movieData.languages && (
                      <div className="flex items-center gap-2">
                        <LuLanguages className="text-lg xl:text-xl" />
                        <p className="text-xs xl:text-sm">
                          {props.movieData.languages.map((l) => l.charAt(0).toUpperCase() + l.slice(1)).join("-")}
                        </p>
                      </div>
                    )}
                    {props.movieData.rip && (
                      <div className="flex items-center gap-2">
                        <MdOutlineHighQuality className="text-lg xl:text-xl" />
                        <p className="text-xs xl:text-sm">{props.movieData.rip}</p>
                      </div>
                    )}
                    {props.movieData.rating && (
                      <div className="flex items-center gap-2">
                        <PiStarFill className="text-yellow-300 text-lg xl:text-xl" />
                        <p className="text-xs xl:text-sm">{props.movieData.rating.toFixed(1)}</p>
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex items-center flex-wrap gap-2 text-primaryTextColor mt-3">
                  <TelegramButton movieData={props.movieData} />
                  <DownloadButton movieData={props.movieData} btnType="Download" />
                </div>
                <div className="mt-3 flex gap-2">
                  <DownloadButton movieData={props.movieData} btnType="MX Player" />
                  <DownloadButton movieData={props.movieData} btnType="PC Player" />
                </div>
              </div>
            </div>
          )}

          {/* ── SERIES METADATA (below player) ── */}
          {props.detailType === "series" && (
            <div className="mb-6">
              {props.movieData.genres && (
                <div className="text-otherColor flex gap-2 flex-wrap text-sm xl:text-md">
                  {props.movieData.genres.map((genre, index) => (
                    <p key={index}>{genre}</p>
                  ))}
                </div>
              )}
              <h1 className="text-primaryTextColor font-extrabold line-clamp-1 text-2xl xl:text-3xl">
                {props.movieData.title}
              </h1>
              {props.movieData.status && (
                <p className="bg-otherColor/40 text-otherColor px-5 rounded-full w-fit line-clamp-1 text-sm xl:text-md mt-1">
                  {props.movieData.status}
                </p>
              )}
              <p className="text-secondaryTextColor line-clamp-3 mt-2 text-xs xl:text-sm">
                {props.movieData.description}
              </p>
              <div className="flex gap-2 text-primaryTextColor flex-wrap mt-2">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex items-center gap-2">
                    <BsListStars className="text-secondaryTextColor text-xl xl:text-2xl" />
                    <p className="text-xs xl:text-sm">{props.movieData.total_seasons} Seasons</p>
                    <span className="text-xs xl:text-sm mx-2">|</span>
                    <p className="text-xs xl:text-sm">{props.movieData.total_episodes} Eps</p>
                  </div>
                  {props.movieData.languages && (
                    <div className="flex items-center gap-2">
                      <LuLanguages className="text-lg xl:text-xl" />
                      <p className="text-xs xl:text-sm">
                        {props.movieData.languages.map((l) => l.charAt(0).toUpperCase() + l.slice(1)).join("-")}
                      </p>
                    </div>
                  )}
                  {props.movieData.rip && (
                    <div className="flex items-center gap-2">
                      <MdOutlineHighQuality className="text-lg xl:text-xl" />
                      <p className="text-xs xl:text-sm">{props.movieData.rip}</p>
                    </div>
                  )}
                  {props.movieData.rating && (
                    <div className="flex items-center gap-2">
                      <PiStarFill className="text-yellow-300 text-lg xl:text-xl" />
                      <p className="text-xs xl:text-sm">{props.movieData.rating.toFixed(1)}</p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ── SEASON TABS ── */}
          {props.detailType === "series" && props.movieData.seasons && (
            <div className="flex gap-2 mb-4 overflow-x-auto pb-2">
              {props.movieData.seasons
                .filter((s) => s.season_number !== 0)
                .sort((a, b) => a.season_number - b.season_number)
                .map((season) => (
                  <button
                    key={season.season_number}
                    onClick={() => {
                      props.setSeasonNumber(season.season_number);
                      setActiveEpisode(null);
                      setExpandedAction({ episode: null, type: null });
                    }}
                    className={`px-4 py-2 rounded-full text-sm font-medium whitespace-nowrap transition-all ${
                      props.seasonNumber === season.season_number
                        ? "bg-otherColor text-bgColor"
                        : "bg-btnColor/70 text-secondaryTextColor hover:text-primaryTextColor hover:bg-btnColor"
                    }`}
                  >
                    Season {season.season_number}
                  </button>
                ))}
            </div>
          )}

          {/* ── EPISODE CARDS ── */}
          {props.detailType === "series" && (
            <div className="flex flex-col gap-3">
              {!props.isEpisodesLoading ? (
                props.episodes &&
                props.episodes
                  .sort((a, b) => a.episode_number - b.episode_number)
                  .map((eps) => {
                    const isActive = activeEpisode?.episode_number === eps.episode_number;
                    const isDownloadOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "download";
                    const isTelegramOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "telegram";
                    const isMxPlayerOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "mxplayer";
                    const isPcPlayerOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "pcplayer";
                    // Look up full episode data with telegram from movieData.seasons
                    const season = props.movieData.seasons?.find((s) => s.season_number === props.seasonNumber);
                    const fullEps = season?.episodes?.find((ep) => ep.episode_number === eps.episode_number) || eps;

                    return (
                      <div key={eps.episode_number}>
                        <div
                          className={`flex items-center gap-3 sm:gap-4 p-3 rounded-xl transition-all duration-300 ${
                            isActive
                              ? "bg-otherColor/20 border border-otherColor/40"
                              : "bg-btnColor/50 hover:bg-btnColor/80"
                          }`}
                        >
                          {/* Thumbnail / number */}
                          <div className="shrink-0 w-20 h-14 sm:w-28 sm:h-16 bg-btnColor rounded-lg flex items-center justify-center overflow-hidden relative">
                            {eps.episode_backdrop ? (
                              <img
                                src={eps.episode_backdrop}
                                alt={eps.title}
                                className="w-full h-full object-cover"
                              />
                            ) : (
                              <span className="text-primaryTextColor font-bold text-sm sm:text-lg">
                                {eps.episode_number}
                              </span>
                            )}
                            {isActive && (
                              <div className="absolute inset-0 flex items-center justify-center bg-black/40">
                                <FaPlay className="text-white text-sm" />
                              </div>
                            )}
                          </div>

                          {/* Info */}
                          <div className="flex-1 min-w-0">
                            <h3 className="text-primaryTextColor text-sm sm:text-base font-semibold line-clamp-1">
                              {eps.episode_number}. {eps.title}
                            </h3>
                            <p className="text-secondaryTextColor text-xs line-clamp-2 mt-0.5 hidden sm:block">
                              {eps.description || eps.overview || ""}
                            </p>
                          </div>

                          {/* Actions */}
                          <div className="shrink-0 flex items-center gap-1.5 sm:gap-2">
                            <button
                              onClick={() => handlePlayEpisode(eps)}
                              className="flex items-center gap-1 px-2.5 sm:px-3 py-1.5 rounded-full bg-otherColor text-bgColor text-[11px] sm:text-xs font-medium hover:opacity-90 transition-opacity"
                            >
                              <BiPlay className="text-sm" /> Play
                            </button>
                            <button
                              onClick={() => toggleAction(eps.episode_number, "download")}
                              className={`flex items-center gap-1 px-2.5 sm:px-3 py-1.5 rounded-full text-[11px] sm:text-xs font-medium transition-all ${
                                isDownloadOpen
                                  ? "bg-primaryBtn text-white"
                                  : "bg-btnColor border border-otherColor/30 text-otherColor hover:bg-otherColor/10"
                              }`}
                            >
                              <FaCloudDownloadAlt className="text-sm" /> Download
                            </button>
                            <button
                              onClick={() => toggleAction(eps.episode_number, "telegram")}
                              className={`flex items-center gap-1 px-2.5 sm:px-3 py-1.5 rounded-full text-[11px] sm:text-xs font-medium transition-all ${
                                isTelegramOpen
                                  ? "bg-primaryBtn text-white"
                                  : "bg-btnColor border border-otherColor/30 text-otherColor hover:bg-otherColor/10"
                              }`}
                            >
                              <PiTelegramLogo className="text-sm" /> Telegram
                            </button>
                            <button
                              onClick={() => toggleAction(eps.episode_number, "mxplayer")}
                              className={`flex items-center gap-1 px-2.5 sm:px-3 py-1.5 rounded-full text-[11px] sm:text-xs font-medium transition-all ${
                                isMxPlayerOpen
                                  ? "bg-primaryBtn text-white"
                                  : "bg-btnColor border border-otherColor/30 text-otherColor hover:bg-otherColor/10"
                              }`}
                            >
                              <FaPlay className="text-sm" /> Player
                            </button>
                            <button
                              onClick={() => toggleAction(eps.episode_number, "pcplayer")}
                              className={`flex items-center gap-1 px-2.5 sm:px-3 py-1.5 rounded-full text-[11px] sm:text-xs font-medium transition-all ${
                                isPcPlayerOpen
                                  ? "bg-primaryBtn text-white"
                                  : "bg-btnColor border border-otherColor/30 text-otherColor hover:bg-otherColor/10"
                              }`}
                            >
                              <FaDesktop className="text-sm" /> PC
                            </button>
                          </div>
                        </div>

                        {/* Expanded quality row */}
                        {(isDownloadOpen || isTelegramOpen || isMxPlayerOpen || isPcPlayerOpen) && fullEps.telegram?.length > 0 && (
                          <div className="ml-3 sm:ml-8 mt-2 flex flex-wrap gap-2">
                            {fullEps.telegram.map((q, idx) => {
                              if (isPcPlayerOpen) {
                                return (
                                  <div key={idx} className="flex items-center gap-1">
                                    <span className="text-[10px] text-white/60">{q.quality}</span>
                                    <button
                                      onClick={() => handlePcPlayerM3U(q)}
                                      className="px-2 py-1 rounded-full bg-primaryBtn text-white text-[10px] hover:opacity-90 transition-opacity"
                                    >
                                      M3U
                                    </button>
                                    <button
                                      onClick={() => handlePcPlayerMP4(q)}
                                      className="px-2 py-1 rounded-full bg-otherColor text-white text-[10px] hover:opacity-90 transition-opacity"
                                    >
                                      MP4
                                    </button>
                                  </div>
                                );
                              }
                              return (
                                <button
                                  key={idx}
                                  onClick={() => {
                                    if (isDownloadOpen) {
                                      handleDownload(q);
                                    } else if (isTelegramOpen) {
                                      handleTelegram(props.movieData.tmdb_id, props.seasonNumber, q.quality);
                                    } else if (isMxPlayerOpen) {
                                      handleMxPlayer(q);
                                    }
                                  }}
                                  className="px-3 py-1 rounded-full bg-btnColor text-primaryTextColor text-xs border border-otherColor/20 hover:bg-otherColor/20 transition-colors"
                                >
                                  {q.quality}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })
              ) : (
                <div className="flex justify-center py-10">
                  <div className="loader-episode"></div>
                </div>
              )}
            </div>
          )}
        </>
      ) : (
        <div className="min-h-[50dvh] flex justify-center content-center items-center">
          <div className="loader"></div>
        </div>
      )}

      {/* Watch Movie Popup */}
      {props.detailType === "movie" && (
        <Watch
          isWatchMoviePopupOpen={isWatchMoviePopupOpen}
          id={props.movieData}
          setIsWatchMoviePopupOpen={setIsWatchMoviePopupOpen}
          popUpType="movie"
        />
      )}
    </div>
  );
}
