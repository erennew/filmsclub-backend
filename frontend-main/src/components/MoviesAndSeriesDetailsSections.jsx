import React, { useState, useRef, useMemo, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
/*
 * NextUI is used only by DownloadButton and TelegramButton, both rendered below.
 * The provider therefore lives here rather than in main.jsx: mounting it at the
 * root pulled vendor-nextui (237 KB) and its @react-aria/@react-stately deps
 * into the entry graph, so every page — including the homepage, which renders
 * no NextUI component at all — preloaded and parsed it before first paint.
 */
import { NextUIProvider } from "@nextui-org/system";
import Watch from "./Watch";
import InlinePlayer from "./InlinePlayer";
import { LazyLoadImage } from "react-lazy-load-image-component";
import "react-lazy-load-image-component/src/effects/black-and-white.css";
import { BiPlay, BiTime } from "react-icons/bi";
import { FiCalendar, FiSearch } from "react-icons/fi";
import { BsListStars, BsPlayFill } from "react-icons/bs";
import { PiStarFill } from "react-icons/pi";
import { LuLanguages } from "react-icons/lu";
import { FaCloudDownloadAlt, FaPlay, FaDesktop } from "react-icons/fa";
import { PiTelegramLogo } from "react-icons/pi";
import { HiChevronLeft, HiChevronRight } from "react-icons/hi2";
import { MdOutlineHighQuality } from "react-icons/md";
import TelegramButton from "./TelegramButtons";
import DownloadButton from "./Buttons";
import { BASE_URL as BASE, TG_USERNAME as USERNAME } from "../config";
import { mediaUrl, parseQuality } from "../utils/media";
import { backdropUrl } from "../utils/imageUrl";
import CastSection from "./CastSection";
import AudioTracks from "./AudioTracks";
import UpcomingEpisodeCard from "./UpcomingEpisodeCard";
import QualityTab from "./QualityTab";
import MobilePlayBar from "./MobilePlayBar";
import WatchlistButton from "./WatchlistButton";

// ── Loading Skeleton ──────────────────────────────────────────────
function DetailSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="aspect-video w-full rounded-[4px] bg-carbon skeleton" />
      <div className="space-y-3">
        <div className="h-8 w-2/3 rounded-[3px] bg-carbon skeleton" />
        <div className="h-4 w-1/3 rounded-[2px] bg-carbon/70 skeleton" />
        <div className="h-4 w-full rounded-[2px] bg-carbon/70 skeleton" />
        <div className="h-4 w-5/6 rounded-[2px] bg-carbon/70 skeleton" />
        <div className="flex gap-2 pt-2">
          <div className="h-10 w-32 rounded-[3px] bg-carbon skeleton" />
          <div className="h-10 w-28 rounded-[3px] bg-carbon/70 skeleton" />
        </div>
      </div>
    </div>
  );
}

const LANG_SHORT_MAP = {
  en: "En", english: "En",
  te: "Te", telugu: "Te",
  ta: "Ta", tamil: "Ta",
  hi: "Hi", hindi: "Hi",
  ml: "Ml", malayalam: "Ml",
  kn: "Kn", kannada: "Kn",
  bn: "Ben", bengali: "Ben",
  mr: "Mr", marathi: "Mr",
  pa: "Pun", punjabi: "Pun",
  ur: "Ur", urdu: "Ur",
  es: "Spa", spanish: "Spa",
  ja: "Jap", japanese: "Jap",
  ko: "Kor", korean: "Kor",
};

function formatLangShort(lang) {
  if (!lang) return "";
  const key = lang.toLowerCase().trim();
  return LANG_SHORT_MAP[key] || (lang.charAt(0).toUpperCase() + lang.slice(1));
}

// ── Meta Badge ────────────────────────────────────────────────────
function MetaBadge({ children, className = "" }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-[2px] text-[10px] font-medium bg-bone/5 border border-bone/10 text-bone/60 ${className}`}>
      {children}
    </span>
  );
}

// ── Episode Action Button ─────────────────────────────────────────
function EpisodeActionBtn({ active, onClick, icon: Icon, label, className = "" }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`
        flex items-center gap-1.5 px-2.5 py-1.5 rounded-[3px] text-[11px] font-semibold
        transition-colors duration-200 border
        ${active
          ? "bg-projector border-projector text-ink"
          : "bg-bone/5 border-bone/10 text-bone/60 hover:bg-bone/10 hover:text-bone hover:border-bone/20"
        }
        ${className}
      `}
    >
      <Icon className="text-xs shrink-0" />
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

// ── Quality Pill ──────────────────────────────────────────────────
function QualityPill({ quality, onClick }) {
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 rounded-[3px] timecode text-[10px] font-bold bg-projector/10 border border-projector/25 text-projector hover:bg-projector/20 transition-colors duration-200"
    >
      {quality}
    </button>
  );
}

export default function MoviesAndSeriesDetailsSections(props) {
  const [isWatchMoviePopupOpen, setIsWatchMoviePopupOpen] = useState(false);
  const [activeEpisode, setActiveEpisode] = useState(null);
  const [expandedAction, setExpandedAction] = useState({ episode: null, type: null });
  const playerWrapperRef = useRef(null);
  const moviePlayerRef = useRef(null);
  const episodeListRef = useRef(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [selectedQuality, setSelectedQuality] = useState("");
  const episodesPerPage = 50;

  const isAnime = props.movieData.is_anime || false;
  const totalEpisodes = props.episodes?.length || 0;

  // Unique qualities from episodes.
  //
  // This previously skipped 480p/720p/1080p for series, so a season whose files
  // were all 1080p produced an empty quality list. QualityTab returns null when
  // that list is empty, which silently removed the entire download / Telegram /
  // subtitle block for most seasons. Series now surface every quality, ordered.
  const qualities = useMemo(() => {
    const qualitySet = new Set();
    const season = props.movieData.seasons?.find(
      (s) => s.season_number === props.seasonNumber
    );

    const addFrom = (sources) => {
      sources?.forEach((q) => {
        if (q?.quality) qualitySet.add(q.quality);
      });
    };

    props.episodes?.forEach((ep) => {
      const fullEps = season?.episodes?.find(
        (e) => e.episode_number === ep.episode_number
      );
      addFrom(fullEps?.sources);
    });

    // Movies (and series where the episode list is empty) read files directly
    // off the media document rather than per-episode sources.
    if (qualitySet.size === 0) {
      addFrom(props.movieData?.telegram);
    }

    return Array.from(qualitySet).sort((a, b) => {
      const order = { "480p": 1, "720p": 2, "1080p": 3, "2160p": 4 };
      return (order[a] || 99) - (order[b] || 99);
    });
  }, [props.episodes, props.movieData.seasons, props.movieData.telegram, props.seasonNumber]);

  const telegramByQuality = useMemo(() => {
    const grouped = {};
    qualities.forEach((q) => (grouped[q] = []));
    const season = props.movieData.seasons?.find((s) => s.season_number === props.seasonNumber);

    if (season?.episodes) {
      season.episodes.forEach((ep) => {
        ep.sources?.forEach((file) => {
          if (grouped[file.quality]) {
            grouped[file.quality].push({ ...file, episode_number: ep.episode_number, episode_title: ep.title || ep.name });
          }
        });
      });
    }

    // Movies keep their files on the media document, not on episodes.
    const hasFiles = Object.values(grouped).some((list) => list.length > 0);
    if (!hasFiles) {
      (props.movieData?.telegram || []).forEach((file) => {
        const quality = file?.quality;
        if (quality && grouped[quality]) grouped[quality].push({ ...file });
      });
    }

    return grouped;
  }, [qualities, props.movieData.seasons, props.movieData.telegram, props.seasonNumber]);

  const filteredEpisodes = useMemo(() => {
    if (!searchQuery) return props.episodes || [];
    const q = searchQuery.toLowerCase();
    return (props.episodes || []).filter(
      (ep) => (ep.title || "").toLowerCase().includes(q) || (ep.description || "").toLowerCase().includes(q) || ep.episode_number.toString().includes(q)
    );
  }, [props.episodes, searchQuery]);

  const paginatedEpisodes = useMemo(() => {
    const start = (currentPage - 1) * episodesPerPage;
    return filteredEpisodes.slice(start, start + episodesPerPage);
  }, [filteredEpisodes, currentPage, episodesPerPage]);

  const totalPages = Math.ceil(filteredEpisodes.length / episodesPerPage);

  const movieAudioTracks = useMemo(() => {
    if (props.movieData.sources?.[0]?.audio_tracks?.length > 0) {
      return props.movieData.sources[0].audio_tracks;
    }
    if (props.movieData.audio_tracks?.length > 0) {
      return props.movieData.audio_tracks;
    }
    if (props.movieData.languages?.length > 0) {
      return props.movieData.languages;
    }
    return [];
  }, [props.movieData]);

  const seriesAudioTracks = useMemo(() => {
    if (activeEpisode?.sources?.[0]?.audio_tracks?.length > 0) {
      return activeEpisode.sources[0].audio_tracks;
    }
    if (activeEpisode?.audio_tracks?.length > 0) {
      return activeEpisode.audio_tracks;
    }
    if (props.movieData.languages?.length > 0) {
      return props.movieData.languages;
    }
    return [];
  }, [activeEpisode, props.movieData.languages]);

  const handlePlayEpisode = useCallback((eps) => {
    const season = props.movieData.seasons?.find((s) => s.season_number === props.seasonNumber);
    const fullEpisode = season?.episodes?.find((ep) => ep.episode_number === eps.episode_number);
    setActiveEpisode(fullEpisode || eps);
    props.setEpisodeNumber(eps.episode_number);
    setTimeout(() => {
      playerWrapperRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }, [props]);

  const handleSeasonChange = useCallback((sn) => {
    props.setSeasonNumber(sn);
    setActiveEpisode(null);
    setExpandedAction({ episode: null, type: null });
    setCurrentPage(1);
    setSearchQuery("");
  }, [props]);

  const toggleAction = useCallback((epNum, type) => {
    setExpandedAction((prev) =>
      prev.episode === epNum && prev.type === type ? { episode: null, type: null } : { episode: epNum, type }
    );
  }, []);

  const handleDownload = useCallback((q) => {
    const url = `${BASE}/dl/${q.id}/${encodeURIComponent(q.name)}`;
    const a = document.createElement("a");
    a.href = url; a.download = q.name;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  }, []);

  const handleTelegram = useCallback((tmdbId, seasonNum, quality) => {
    window.open(`https://t.me/${USERNAME}?start=file_${tmdbId}_${seasonNum}_${quality}`, "_blank", "noopener noreferrer");
  }, []);

  const handleMxPlayer = useCallback((q) => {
    const url = `${BASE}/dl/${q.id}/${encodeURIComponent(q.name)}`;
    window.open(`intent:${url}#Intent;type=video/x-matroska;action=android.intent.action.VIEW;end;`, "_blank", "noopener,noreferrer");
  }, []);

  const handlePcPlayerM3U = useCallback((q) => {
    const isMobile = /Android|iPhone|iPad/i.test(navigator.userAgent);
    const streamUrl = `${BASE}/dl/${q.id}/${encodeURIComponent(q.name)}`;
    const safeName = q.name.replace(/[^a-zA-Z0-9]/g, "_");
    if (isMobile) {
      const a = document.createElement("a"); a.href = streamUrl; a.download = q.name;
      document.body.appendChild(a); a.click(); document.body.removeChild(a); return;
    }
    try {
      window.location.href = `vlc://${encodeURIComponent(streamUrl)}`;
      setTimeout(() => {
        if (document.hasFocus()) {
          const m3u = `#EXTM3U\n#EXTINF:-1,${safeName}\n${streamUrl}`;
          const blob = new Blob([m3u], { type: "audio/x-mpegurl" });
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a"); a.href = url; a.download = `${safeName}.m3u`;
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
          setTimeout(() => URL.revokeObjectURL(url), 100);
        }
      }, 1000);
    } catch {
      const m3u = `#EXTM3U\n#EXTINF:-1,${safeName}\n${streamUrl}`;
      const blob = new Blob([m3u], { type: "audio/x-mpegurl" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = `${safeName}.m3u`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 100);
    }
  }, []);

  const playerSources = activeEpisode?.sources?.length > 0
    ? activeEpisode.sources.map((q) => ({ src: mediaUrl(q), type: "video/mp4", size: parseQuality(q.quality) }))
    : [];
  const playerPoster = activeEpisode?.episode_backdrop || props.movieData?.backdrop || "";
  const playerFileId = activeEpisode?.sources?.[0]?.id || "";
  const playerFileName = activeEpisode?.sources?.[0]?.name || "";

  const hasPlayableSource =
    props.detailType === "movie"
      ? props.movieData.sources?.length > 0
      : totalEpisodes > 0;

  const mobileBarMeta =
    props.detailType === "movie"
      ? [
          props.movieData.release_year || props.movieData.year,
          props.movieData.runtime && `${props.movieData.runtime} min`,
          props.movieData.rip || props.movieData.quality,
        ]
          .filter(Boolean)
          .join(" · ")
      : activeEpisode
        ? `S${props.seasonNumber} · E${String(activeEpisode.episode_number).padStart(2, "0")}${
            activeEpisode.title ? ` · ${activeEpisode.title}` : ""
          }`
        : [
            props.movieData.total_seasons && `${props.movieData.total_seasons} seasons`,
            props.movieData.total_episodes && `${props.movieData.total_episodes} episodes`,
          ]
            .filter(Boolean)
            .join(" · ");

  const handleMobilePlay = () => {
    const target =
      props.detailType === "movie"
        ? moviePlayerRef
        : activeEpisode
          ? playerWrapperRef
          : episodeListRef;
    target.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // ── RENDER ────────────────────────────────────────────────────────
  if (props.isMovieDataLoading) {
    return (
      <div className="mt-4">
        <DetailSkeleton />
      </div>
    );
  }

  return (
    <NextUIProvider>
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.4 }}
      className="mt-4 space-y-6"
    >
      {/* ── VIDEO PLAYER (SERIES) ───────────────────────────── */}
      {props.detailType === "series" && (
        <div
          ref={playerWrapperRef}
          id="cx-player"
          data-cx-player
          className="w-full scroll-mt-24"
        >
          {playerSources.length > 0 ? (
            <InlinePlayer
              key={playerFileId || "no-episode"}
              sources={playerSources}
              poster={playerPoster}
              fileId={playerFileId}
              fileName={playerFileName}
            />
          ) : (
            <div className="aspect-video w-full relative flex items-center justify-center bg-carbon rounded-[4px] overflow-hidden border border-bone/10">
              {props.movieData.backdrop && (
                <LazyLoadImage
                  src={backdropUrl(props.movieData.backdrop, 640)}
                  effect="black-and-white"
                  alt={props.movieData.title}
                  className="absolute inset-0 w-full h-full object-cover opacity-20 grayscale"
                />
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-ink via-ink/40 to-transparent" />
              <div className="relative z-10 flex flex-col items-center text-center px-6">
                <span className="eyebrow mb-3">Screen one</span>
                <p className="font-display text-xl sm:text-2xl text-bone/90">
                  Pick an episode to start the screening
                </p>
                <p className="mt-2 max-w-sm text-[12px] sm:text-[13px] text-bone/50">
                  Episodes are listed below. Choose a season, then press play.
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── VIDEO PLAYER (MOVIE) ────────────────────────────── */}
      {props.detailType === "movie" && (
        <div className="w-full scroll-mt-24" ref={moviePlayerRef}>
          {props.movieData.sources?.length > 0 ? (
            <InlinePlayer
              key={props.movieData.tmdb_id}
              sources={props.movieData.sources.map((q) => ({
                src: mediaUrl(q),
                type: "video/mp4",
                size: parseQuality(q.quality),
              }))}
              poster={props.movieData.backdrop || ""}
              fileId={props.movieData.sources[0]?.id || ""}
              fileName={props.movieData.sources[0]?.name || ""}
            />
          ) : (
            <div className="aspect-video w-full relative flex items-center justify-center bg-carbon rounded-[4px] overflow-hidden border border-bone/10">
              {props.movieData.backdrop && (
                <LazyLoadImage
                  src={backdropUrl(props.movieData.backdrop, 640)}
                  effect="black-and-white"
                  alt={props.movieData.title}
                  className="absolute inset-0 w-full h-full object-cover opacity-15 grayscale"
                />
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-ink via-ink/40 to-transparent" />
              <div className="relative z-10 flex flex-col items-center text-center px-6">
                <span className="eyebrow mb-3">No print available</span>
                <p className="font-display text-xl sm:text-2xl text-bone/90">
                  This title hasn&rsquo;t been screened yet
                </p>
                <p className="mt-2 max-w-sm text-[12px] sm:text-[13px] text-bone/50">
                  The print is still being prepared. Join our Telegram channel and
                  we&rsquo;ll announce it the moment it&rsquo;s ready.
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── MOVIE METADATA ──────────────────────────────────── */}
      {props.detailType === "movie" && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          className="space-y-4"
        >
          {/* Genres */}
          {props.movieData.genres?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {props.movieData.genres.slice(0, 4).map((genre, i) => (
                <span key={i} className="genre-badge">{genre}</span>
              ))}
            </div>
          )}

          {/* Title */}
          <h1 className="font-display text-3xl sm:text-4xl md:text-5xl font-medium text-bone leading-[1.05] tracking-tight">
            {props.movieData.title}
          </h1>

          {/* Meta row */}
          <div className="flex flex-wrap items-center gap-1.5">
            {(props.movieData.rating || props.movieData.rate) && (
              <div className="rating-badge">
                <PiStarFill className="text-[10px] text-projector" />
                {Number(props.movieData.rating || props.movieData.rate).toFixed(1)}
              </div>
            )}
            {(props.movieData.release_year || props.movieData.year) && (
              <MetaBadge>{props.movieData.release_year || props.movieData.year}</MetaBadge>
            )}
            {props.movieData.runtime && (
              <MetaBadge><BiTime className="text-xs text-bone/50" />{props.movieData.runtime} min</MetaBadge>
            )}
            {(props.movieData.rip || props.movieData.quality) && (
              <MetaBadge className="!border-projector/30 !text-projector font-semibold">
                {props.movieData.rip || props.movieData.quality}
              </MetaBadge>
            )}
            {props.movieData.languages?.length > 0 && (
              <MetaBadge className="!bg-bone/8 !border-bone/15 !text-bone/85">
                <LuLanguages className="text-xs text-projector" />
                {props.movieData.languages.map(formatLangShort).join(" / ")}
              </MetaBadge>
            )}
          </div>

          {/* Description */}
          {props.movieData.description && (
            <p className="text-bone/65 text-sm md:text-base leading-relaxed max-w-3xl line-clamp-4">
              {props.movieData.description}
            </p>
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap gap-2 pt-1">
            <TelegramButton movieData={props.movieData} />
            <DownloadButton movieData={props.movieData} btnType="Download" />
            <WatchlistButton
              item={{ ...props.movieData, media_type: "movie" }}
              variant="detail"
            />
            {props.movieData.sources?.length > 0 && (
              <>
                <button
                  onClick={() => handleMxPlayer(props.movieData.sources[0])}
                  className="flex items-center gap-2 px-4 py-2 bg-bone/5 border border-bone/10 rounded-[3px] text-bone/60 text-xs font-semibold hover:bg-bone/10 hover:text-bone transition-colors duration-200"
                >
                  <FaPlay className="text-xs" /> Player
                </button>
                <button
                  onClick={() => handlePcPlayerM3U(props.movieData.sources[0])}
                  className="flex items-center gap-2 px-4 py-2 bg-bone/5 border border-bone/10 rounded-[3px] text-bone/60 text-xs font-semibold hover:bg-bone/10 hover:text-bone transition-colors duration-200"
                >
                  <FaDesktop className="text-xs" /> PC Player
                </button>
              </>
            )}
          </div>

          {/* Cast */}
          {(props.movieData.cast?.length > 0 || props.movieData.crew?.length > 0) && (
            <CastSection cast={props.movieData.cast} crew={props.movieData.crew} />
          )}

          {/* Streaming Tip */}
          <div className="px-4 py-3.5 bg-projector/[0.06] border-l-2 border-projector/60 rounded-r-[3px] flex items-start gap-3">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-projector/80 flex-shrink-0 mt-0.5">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
            <p className="text-[12px] md:text-[13px] text-bone/55 leading-relaxed">
              <span className="timecode text-[10px] uppercase tracking-[0.14em] text-projector mr-1.5">Streaming tip</span>
              If you can&rsquo;t stream smoothly or want to switch audio/subtitle tracks, use the{" "}
              <span className="text-bone/85 font-medium">Mobile Player</span> or{" "}
              <span className="text-bone/85 font-medium">PC Player</span> buttons for better performance and more options.
            </p>
          </div>
        </motion.div>
      )}

      {/* ── SERIES METADATA ─────────────────────────────────── */}
      {props.detailType === "series" && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          className="space-y-3"
        >
          {/* Genres */}
          {props.movieData.genres?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {props.movieData.genres.map((g, i) => <span key={i} className="genre-badge">{g}</span>)}
            </div>
          )}
          {/* Title */}
          <h1 className="font-display text-3xl sm:text-4xl font-medium text-bone tracking-tight leading-[1.05]">
            {props.movieData.title}
          </h1>
          {/* Status + meta */}
          <div className="flex flex-wrap items-center gap-1.5">
            {props.movieData.status && (
              <span className="px-2 py-1 rounded-[2px] timecode text-[10px] uppercase tracking-[0.12em] font-semibold bg-projector/10 border border-projector/25 text-projector">
                {props.movieData.status}
              </span>
            )}
            {(props.movieData.rating || props.movieData.rate) && (
              <div className="rating-badge">
                <PiStarFill className="text-[10px] text-projector" />
                {Number(props.movieData.rating || props.movieData.rate).toFixed(1)}
              </div>
            )}
            {(props.movieData.release_year || props.movieData.year) && (
              <MetaBadge>{props.movieData.release_year || props.movieData.year}</MetaBadge>
            )}
            {props.movieData.total_seasons && (
              <MetaBadge><BsListStars className="text-xs text-bone/50" />{props.movieData.total_seasons} Seasons</MetaBadge>
            )}
            {props.movieData.total_episodes && (
              <MetaBadge>{props.movieData.total_episodes} Episodes</MetaBadge>
            )}
            {(props.movieData.rip || props.movieData.quality) && (
              <MetaBadge className="!border-projector/30 !text-projector font-semibold">
                {props.movieData.rip || props.movieData.quality}
              </MetaBadge>
            )}
            {props.movieData.languages?.length > 0 && (
              <MetaBadge className="!bg-bone/8 !border-bone/15 !text-bone/85">
                <LuLanguages className="text-xs text-projector" />
                {props.movieData.languages.map(formatLangShort).join(" / ")}
              </MetaBadge>
            )}
          </div>
          {/* Description */}
          {props.movieData.description && (
            <p className="text-bone/65 text-sm md:text-base leading-relaxed max-w-3xl line-clamp-3">
              {props.movieData.description}
            </p>
          )}
          {/* My List */}
          <div className="flex flex-wrap gap-2 pt-1">
            <WatchlistButton
              item={{ ...props.movieData, media_type: "tv" }}
              variant="detail"
            />
          </div>
        </motion.div>
      )}

      {/* ── SEASON TABS ─────────────────────────────────────── */}
      {props.detailType === "series" && props.movieData.seasons?.length > 0 && (
        <div className="flex gap-2 overflow-x-auto pb-1 no-scrollbar">
          {props.movieData.seasons
            .filter((s) => s.season_number !== 0)
            .sort((a, b) => a.season_number - b.season_number)
            .map((season) => {
              const isActive = props.seasonNumber === season.season_number;
              return (
                <button
                  key={season.season_number}
                  onClick={() => handleSeasonChange(season.season_number)}
                  className={`
                    flex-shrink-0 px-3.5 py-2 rounded-[3px] timecode text-[10px] uppercase tracking-[0.1em] font-semibold
                    whitespace-nowrap transition-colors duration-200 border
                    ${isActive
                      ? "bg-projector border-projector text-ink"
                      : "bg-bone/5 border-bone/10 text-bone/55 hover:bg-bone/10 hover:text-bone"
                    }
                  `}
                >
                  Season {season.season_number}
                  {season.episode_count && <span className={`ml-1.5 ${isActive ? "text-ink/60" : "text-bone/55"}`}>({season.episode_count})</span>}
                </button>
              );
            })}
        </div>
      )}

      {/* ── EPISODE SEARCH ──────────────────────────────────── */}
      {props.detailType === "series" && totalEpisodes > 10 && (
        <div className="relative">
          <FiSearch className="absolute left-3.5 top-1/2 -translate-y-1/2 text-bone/60 text-sm z-10" />
          <input
            type="text"
            placeholder="Search episodes by title or number…"
            value={searchQuery}
            onChange={(e) => { setSearchQuery(e.target.value); setCurrentPage(1); }}
            className="w-full sm:w-80 pl-10 pr-4 py-2.5 rounded-[3px] border border-bone/10 text-bone text-sm placeholder-bone/35 focus:outline-none focus:border-projector/60 transition-colors duration-200"
            autoComplete="off"
          />
        </div>
      )}

      {/* ── QUALITY TABS (SERIES) ────────────────────────────── */}
      {props.detailType === "series" && qualities.length > 0 && (
        <QualityTab
          qualities={qualities}
          selectedQuality={selectedQuality}
          setSelectedQuality={setSelectedQuality}
          telegramByQuality={telegramByQuality}
          tmdbId={props.movieData.tmdb_id}
          seasonNumber={props.seasonNumber}
          episodeNumber={activeEpisode?.episode_number}
          db_index={props.movieData.db_index}
        />
      )}

      {/* ── UPCOMING EPISODE ────────────────────────────────── */}
      {props.detailType === "series" && props.movieData.next_episode_to_air && (
        <UpcomingEpisodeCard episode={props.movieData.next_episode_to_air} />
      )}

      {/* ── CAST (SERIES) ───────────────────────────────────── */}
      {props.detailType === "series" &&
        (props.movieData.cast?.length > 0 || props.movieData.crew?.length > 0) && (
          <CastSection cast={props.movieData.cast} crew={props.movieData.crew} />
        )}

      {/* ── STREAMING TIP (SERIES) ──────────────────────────── */}
      {props.detailType === "series" && (
        <div className="px-4 py-3.5 bg-projector/[0.06] border-l-2 border-projector/60 rounded-r-[3px] flex items-start gap-3">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-projector/80 flex-shrink-0 mt-0.5">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          <p className="text-[12px] md:text-[13px] text-bone/55 leading-relaxed">
            <span className="timecode text-[10px] uppercase tracking-[0.14em] text-projector mr-1.5">Streaming tip</span>
            If you can&rsquo;t stream smoothly or want to switch audio/subtitle tracks, use the{" "}
            <span className="text-bone/85 font-medium">Mobile Player</span> or{" "}
            <span className="text-bone/85 font-medium">PC Player</span> buttons for better performance and more options.
          </p>
        </div>
      )}


      {/* ── EPISODE LIST ────────────────────────────────────── */}
      {props.detailType === "series" && (
        <div className="space-y-2 scroll-mt-24" ref={episodeListRef}>
          {/* List header */}
          <div className="flex items-end justify-between gap-4 border-b border-bone/10 pb-3 mb-4">
            <div>
              <span className="eyebrow mb-2">Programme</span>
              <h2 className="section-title text-xl md:text-2xl leading-none">Episodes</h2>
            </div>
            {filteredEpisodes.length > 0 && (
              <span className="timecode text-[10px] text-bone/60">
                {filteredEpisodes.length} total
              </span>
            )}
          </div>

          {props.isEpisodesLoading ? (
            <div className="flex justify-center py-16">
              <div className="loader-episode" />
            </div>
          ) : paginatedEpisodes.length === 0 ? (
            <div className="py-12 text-center">
              <p className="text-bone/60 text-sm">No episodes found{searchQuery ? ` for "${searchQuery}"` : ""}.</p>
            </div>
          ) : (
            paginatedEpisodes
              .sort((a, b) => a.episode_number - b.episode_number)
              .map((eps) => {
                const isActive = activeEpisode?.episode_number === eps.episode_number;
                const isDownloadOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "download";
                const isTelegramOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "telegram";
                const isMxPlayerOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "mxplayer";
                const isPcPlayerOpen = expandedAction.episode === eps.episode_number && expandedAction.type === "pcplayer";
                const season = props.movieData.seasons?.find((s) => s.season_number === props.seasonNumber);
                const fullEps = season?.episodes?.find((ep) => ep.episode_number === eps.episode_number) || eps;
                const hasVideo = fullEps.sources?.length > 0;

                return (
                  <div key={eps.episode_number}>
                    <motion.div
                      initial={{ opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      className={`
                        flex items-start gap-3 p-3 sm:p-4 rounded-[4px] border transition-colors duration-200 cursor-default
                        ${isActive
                          ? "bg-projector/[0.07] border-projector/25"
                          : "bg-bone/[0.03] border-bone/8 hover:bg-bone/[0.06] hover:border-bone/15"
                        }
                      `}
                    >
                      {/* Thumbnail / Episode number */}
                      <div className="flex-shrink-0 relative w-20 h-12 sm:w-28 sm:h-16 rounded-[3px] overflow-hidden bg-carbon flex items-center justify-center">
                        {eps.episode_backdrop ? (
                          <>
                            <img src={eps.episode_backdrop} alt={eps.title} className="w-full h-full object-cover" />
                            {isActive && (
                              <div className="absolute inset-0 bg-ink/55 flex items-center justify-center">
                                <BsPlayFill className="text-projector text-xl" />
                              </div>
                            )}
                          </>
                        ) : (
                          <span className="timecode text-xl sm:text-2xl font-black text-bone/50">
                            {String(eps.episode_number).padStart(2, "0")}
                          </span>
                        )}
                        {!hasVideo && (
                          <div className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-bone/25" title="No video" />
                        )}
                      </div>

                      {/* Info + Actions */}
                      <div className="flex-1 min-w-0 space-y-1.5">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <h3 className="text-bone text-xs sm:text-sm font-semibold line-clamp-1">
                              <span className="timecode text-bone/55 mr-1.5">E{String(eps.episode_number).padStart(2, "0")}</span>
                              {eps.title}
                            </h3>
                            {(eps.description || eps.overview) && (
                              <p className="text-bone/60 text-xs line-clamp-2 mt-0.5 hidden sm:block leading-relaxed">
                                {eps.description || eps.overview}
                              </p>
                            )}
                          </div>
                        </div>

                        {/* Action buttons */}
                        <div className="flex flex-wrap items-center gap-1.5">
                          <EpisodeActionBtn
                            icon={BsPlayFill}
                            label="Play"
                            onClick={() => handlePlayEpisode(eps)}
                            active={isActive}
                            className="bg-projector border-projector !text-ink hover:bg-[#F0CA98] hover:border-[#F0CA98]"
                          />
                          {hasVideo && (
                            <>
                              <EpisodeActionBtn icon={FaCloudDownloadAlt} label="Download" active={isDownloadOpen} onClick={() => toggleAction(eps.episode_number, "download")} />
                              <EpisodeActionBtn icon={PiTelegramLogo} label="Telegram" active={isTelegramOpen} onClick={() => toggleAction(eps.episode_number, "telegram")} />
                              <EpisodeActionBtn icon={FaPlay} label="Player" active={isMxPlayerOpen} onClick={() => toggleAction(eps.episode_number, "mxplayer")} />
                              <EpisodeActionBtn icon={FaDesktop} label="PC" active={isPcPlayerOpen} onClick={() => toggleAction(eps.episode_number, "pcplayer")} />
                            </>
                          )}
                        </div>
                      </div>
                    </motion.div>

                    {/* Expanded quality row */}
                    <AnimatePresence>
                      {(isDownloadOpen || isTelegramOpen || isMxPlayerOpen || isPcPlayerOpen) && fullEps.sources?.length > 0 && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.2 }}
                          className="overflow-hidden"
                        >
                          <div className="flex flex-wrap gap-2 px-4 py-3 bg-bone/[0.03] rounded-b-[4px] border-x border-b border-bone/8 -mt-1">
                            {fullEps.sources.map((q, idx) => {
                              if (isPcPlayerOpen) {
                                return (
                                  <div key={idx} className="flex items-center gap-1.5">
                                    <span className="timecode text-[10px] text-bone/60 font-medium">{q.quality}</span>
                                    <button onClick={() => handlePcPlayerM3U(q)} className="px-2.5 py-1 rounded-[2px] bg-projector text-ink text-[10px] font-bold hover:bg-[#F0CA98] transition-colors">M3U</button>
                                  </div>
                                );
                              }
                              return (
                                <QualityPill
                                  key={idx}
                                  quality={q.quality}
                                  onClick={() => {
                                    if (isDownloadOpen) handleDownload(q);
                                    else if (isTelegramOpen) handleTelegram(props.movieData.tmdb_id, props.seasonNumber, q.quality);
                                    else if (isMxPlayerOpen) handleMxPlayer(q);
                                  }}
                                />
                              );
                            })}
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                );
              })
          )}

          {/* Episode pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4 pt-4 border-t border-bone/8">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="flex items-center gap-1 px-3 py-1.5 rounded-[3px] bg-bone/5 border border-bone/10 text-bone/60 text-xs font-semibold disabled:opacity-30 hover:bg-bone/10 hover:text-bone transition-colors duration-200 disabled:cursor-not-allowed"
              >
                <HiChevronLeft className="text-sm" /> Prev
              </button>
              <span className="timecode text-[10px] text-bone/60">Page {currentPage} / {totalPages}</span>
              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="flex items-center gap-1 px-3 py-1.5 rounded-[3px] bg-bone/5 border border-bone/10 text-bone/60 text-xs font-semibold disabled:opacity-30 hover:bg-bone/10 hover:text-bone transition-colors duration-200 disabled:cursor-not-allowed"
              >
                Next <HiChevronRight className="text-sm" />
              </button>
            </div>
          )}
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

      {!props.isMovieDataLoading && hasPlayableSource && (
        <MobilePlayBar
          title={props.movieData.title || "Now screening"}
          meta={mobileBarMeta}
          label={
            props.detailType === "movie"
              ? "Play"
              : activeEpisode
                ? "Play episode"
                : "Pick episode"
          }
          onPlay={handleMobilePlay}
        />
      )}
    </motion.div>
    </NextUIProvider>
  );
}
