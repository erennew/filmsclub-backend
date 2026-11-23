import { AiOutlineClose } from "react-icons/ai";
import { motion, AnimatePresence } from "framer-motion";
import Plyr from "plyr-react";
import "plyr-react/plyr.css";
import { useEffect, useState, useRef, useMemo } from "react";
import { useLocation } from "react-router-dom";
import { BASE_URL as BASE } from "../config";
import { mediaUrl, parseQuality } from "../utils/media";

export default function WatchTrailer(props) {
  const [sources, setSources] = useState([]);
  const [poster, setPoster] = useState("");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [audioTracks, setAudioTracks] = useState([]);
  const [currentAudioTrack, setCurrentAudioTrack] = useState(0);
  const [subtitleTracks, setSubtitleTracks] = useState([]);
  const [currentSubtitleTrack, setCurrentSubtitleTrack] = useState(-1);
  const [isMiniPlayer, setIsMiniPlayer] = useState(false);
  const [showAudioDropdown, setShowAudioDropdown] = useState(false);
  const [showSubDropdown, setShowSubDropdown] = useState(false);
  const [currentFile, setCurrentFile] = useState({ id: "", name: "" });

  const playerRef = useRef(null);
  const audioDropdownRef = useRef(null);
  const subDropdownRef = useRef(null);
  const location = useLocation();

  // Reset state when modal closes
  useEffect(() => {
    if (!props.isWatchMoviePopupOpen && !props.isWatchEpisodePopupOpen) {
      setAudioTracks([]);
      setCurrentAudioTrack(0);
      setSubtitleTracks([]);
      setCurrentSubtitleTrack(-1);
      setIsModalOpen(false);
      setIsMiniPlayer(false);
      setShowAudioDropdown(false);
      setShowSubDropdown(false);
      setCurrentFile({ id: "", name: "" });
    }
  }, [props.isWatchMoviePopupOpen, props.isWatchEpisodePopupOpen]);

  // Fetch media info (audio + subtitles) when video loads
  useEffect(() => {
    const fetchMediaInfo = async () => {
      if (!sources.length || !props.id) return;

      try {
        let fileId, fileName;
        if (props.popUpType === "movie" && props.id.sources?.length > 0) {
          fileId = props.id.sources[0].id;
          fileName = props.id.sources[0].name;
        } else if (props.popUpType === "episode") {
          const season = props.id.seasons?.find((s) => s.season_number === props.seasonNumber);
          const episode = season?.episodes?.find((ep) => ep.episode_number === props.episodeNumber);
          if (episode?.sources?.length > 0) {
            fileId = episode.sources[0].id;
            fileName = episode.sources[0].name;
          }
        }
        if (!fileId || !fileName) return;
        setCurrentFile({ id: fileId, name: fileName });

        const response = await fetch(
          `${BASE}/api/media-info/${fileId}?name=${encodeURIComponent(fileName)}`
        );
        if (!response.ok) return;
        const data = await response.json();

        // Audio tracks
        if (data.audio_tracks?.length > 1) {
          setAudioTracks(data.audio_tracks);
          const defaultIndex = data.audio_tracks.findIndex((t) => t.is_default);
          setCurrentAudioTrack(defaultIndex >= 0 ? defaultIndex : 0);
        }

        // Combine subtitle tracks (embedded + external)
        const allSubs = [];
        if (data.subtitle_tracks?.length > 0) {
          allSubs.push(...data.subtitle_tracks.map((t) => ({ ...t, type: 'embedded' })));
        }
        if (data.external_subtitles?.length > 0) {
          data.external_subtitles.forEach((s) => {
            allSubs.push({
              index: allSubs.length,
              stream_index: -1,
              codec: s.format,
              language: s.language,
              title: s.label || s.language.toUpperCase(),
              is_text: true,
              is_default: s.is_default,
              is_forced: false,
              type: 'external',
              file_id: s.id,
              file_name: s.name,
            });
          });
        }
        setSubtitleTracks(allSubs);
      } catch (error) {
        console.error("Error fetching media info:", error);
      }
    };

    const timer = setTimeout(fetchMediaInfo, 500);
    return () => clearTimeout(timer);
  }, [sources, props.id, props.popUpType, props.seasonNumber, props.episodeNumber, BASE]);

  // Build source URL with audio/subtitle track params
  const buildTrackUrl = (audioIdx, subIdx) => {
    const { id, name } = currentFile;
    if (!id || !name) return "";
    const params = new URLSearchParams();
    params.set("audio", audioIdx);
    if (subIdx >= 0) params.set("subtitle", subIdx);
    return `${BASE}/dl/${id}/${encodeURIComponent(name)}?${params.toString()}`;
  };

  // Switch audio track via backend ffmpeg remux
  const switchAudioTrack = (trackIndex) => {
    const plyr = playerRef.current?.plyr;
    if (!plyr) return;

    const savedTime = plyr.currentTime || 0;
    const wasPlaying = !plyr.paused;

    const newSrc = buildTrackUrl(trackIndex, currentSubtitleTrack);
    if (!newSrc) return;

    plyr.source = {
      type: "video",
      sources: [{ src: newSrc, type: "video/mp4", size: sources[0]?.size || 1080 }],
      poster: poster,
    };

    plyr.once("loadedmetadata", () => {
      plyr.currentTime = savedTime;
      if (wasPlaying) plyr.play();
    });

    setCurrentAudioTrack(trackIndex);
    setShowAudioDropdown(false);
  };

  // Switch subtitle track via backend ffmpeg remux (embedded) or native tracks (external)
  const switchSubtitleTrack = (trackIndex) => {
    const plyr = playerRef.current?.plyr;
    if (!plyr) return;

    // For external subtitles, use native track injection
    const selected = subtitleTracks[trackIndex];
    if (selected && selected.type === 'external') {
      const video = plyr.media;
      const existingLabel = selected.title || selected.language.toUpperCase();
      let found = false;
      for (let i = 0; i < video.textTracks.length; i++) {
        if (video.textTracks[i].label === existingLabel) {
          video.textTracks[i].mode = 'showing';
          found = true;
          break;
        }
      }
      if (!found) {
        const track = document.createElement('track');
        track.kind = 'subtitles';
        track.label = existingLabel;
        track.srclang = selected.language || 'und';
        track.src = `${BASE}/api/subtitle/${selected.file_id}?name=${encodeURIComponent(selected.file_name || '')}`;
        track.default = false;
        video.appendChild(track);
        setTimeout(() => {
          const added = video.textTracks[video.textTracks.length - 1];
          if (added) added.mode = 'showing';
        }, 200);
      }
      setCurrentSubtitleTrack(trackIndex);
      setShowSubDropdown(false);
      return;
    }

    // For embedded subtitles, use backend ffmpeg remux
    const savedTime = plyr.currentTime || 0;
    const wasPlaying = !plyr.paused;

    const newSrc = buildTrackUrl(currentAudioTrack, trackIndex);
    if (!newSrc) return;

    plyr.source = {
      type: "video",
      sources: [{ src: newSrc, type: "video/mp4", size: sources[0]?.size || 1080 }],
      poster: poster,
    };

    plyr.once("loadedmetadata", () => {
      plyr.currentTime = savedTime;
      if (wasPlaying) plyr.play();
    });

    setCurrentSubtitleTrack(trackIndex);
    setShowSubDropdown(false);
  };

  // Close dropdowns on outside click
  useEffect(() => {
    function handleClickOutside(e) {
      if (audioDropdownRef.current && !audioDropdownRef.current.contains(e.target)) {
        setShowAudioDropdown(false);
      }
      if (subDropdownRef.current && !subDropdownRef.current.contains(e.target)) {
        setShowSubDropdown(false);
      }
    }
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      if (props.isWatchMoviePopupOpen || props.isWatchEpisodePopupOpen) {
        try {
          let videoSources = [];
          let selectedPoster = "";

          if (props.popUpType === "movie") {
            if (props.id?.sources?.length > 0) {
              videoSources = props.id.sources.map((q) => ({
                src: mediaUrl(q),
                type: "video/mp4",
                size: parseQuality(q.quality),
              }));
            }
            selectedPoster = props.id?.backdrop || "";
            if (props.id?.tmdb_id) {
              fetch(`${BASE}/api/track-view?tmdb_id=${props.id.tmdb_id}&media_type=movie`, {
                method: "POST",
                keepalive: true,
              }).catch(() => {});
            }
          } else if (props.popUpType === "episode") {
            const season = props.id?.seasons?.find(
              (s) => s.season_number === props.seasonNumber
            );
            if (season) {
              const episode = season.episodes?.find(
                (ep) => ep.episode_number === props.episodeNumber
              );
              if (episode?.sources?.length > 0) {
                videoSources = episode.sources.map((q) => ({
                  src: mediaUrl(q),
                  type: "video/mp4",
                  size: parseQuality(q.quality),
                }));
                selectedPoster = episode.episode_backdrop || "";
              }
            }
            if (props.id?.tmdb_id) {
              fetch(`${BASE}/api/track-view?tmdb_id=${props.id.tmdb_id}&media_type=tv`, {
                method: "POST",
                keepalive: true,
              }).catch(() => {});
            }
          }

          setSources(videoSources);
          setPoster(selectedPoster);
          setIsModalOpen(true);
        } catch (error) {
          console.error("Error processing data:", error);
        }
      }
    };

    fetchData();
  }, [
    props.isWatchMoviePopupOpen,
    props.isWatchEpisodePopupOpen,
    props.popUpType,
    props.id,
    props.seasonNumber,
    props.episodeNumber,
    BASE,
  ]);

  const closeModal = () => {
    setIsModalOpen(false);
    setIsMiniPlayer(false);
    if (props.popUpType === "trailer") {
      props.setIsTrailerPopupOpen(false);
    } else if (props.popUpType === "movie") {
      props.setIsWatchMoviePopupOpen(false);
    } else {
      props.setIsWatchEpisodePopupOpen(false);
    }
  };

  const toggleMiniPlayer = () => {
    setIsMiniPlayer((prev) => !prev);
  };

  const plyrProps = useMemo(() => ({
    source: {
      type: "video",
      sources: sources,
    },
    options: {
      poster: poster,
      settings: ["quality", "speed"],
      controls: [
        "play-large",
        "play",
        "progress",
        "current-time",
        "duration",
        "pip",
        "fullscreen",
      ],
      tooltips: { controls: true, seek: true },
      hideControls: false,
      invertTime: false,
      clickToPlay: true,
      disableContextMenu: false,
      captions: { active: false, update: false, language: 'auto' },
      audio: { active: false },
    },
  }), [sources, poster]);

  return (
    <AnimatePresence mode="wait">
      {isModalOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
          className={`fixed z-40 transition-all duration-500 ${
            isMiniPlayer
              ? "bottom-4 right-4 w-80 sm:w-96 rounded-lg shadow-2xl"
              : "inset-0 w-full h-[100dvh] bg-black/90 backdrop-blur-md flex items-center justify-center"
          }`}
        >
          {/* Close / Minimize buttons */}
          <div className={`absolute z-50 flex gap-2 ${isMiniPlayer ? "top-2 right-2" : "top-5 right-5"}`}>
            {!isMiniPlayer && (
              <button
                onClick={toggleMiniPlayer}
                className="text-white text-lg bg-black/50 hover:bg-black/80 rounded-full p-2"
                title="Mini Player"
              >
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="2" y="2" width="20" height="20" rx="2" />
                  <rect x="14" y="14" width="6" height="6" rx="1" fill="currentColor" stroke="none" />
                </svg>
              </button>
            )}
            <button
              onClick={closeModal}
              className={`text-bone text-lg bg-ink/60 hover:bg-ink/90 rounded-[3px] ${isMiniPlayer ? "p-1.5" : "p-2"}`}
            >
              <AiOutlineClose />
            </button>
          </div>

          <motion.div
            key="watch-modal-content"
            initial={{ scale: isMiniPlayer ? 1 : 0.9 }}
            animate={{ scale: 1 }}
            exit={{ scale: isMiniPlayer ? 1 : 0.9 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
            className={`flex flex-col shadow-lg relative bg-ink ${
              isMiniPlayer ? "w-full rounded-[4px]" : "w-full max-w-5xl rounded-[4px]"
            }`}
          >
            <style>{`
              .plyr {
                --plyr-color-main: #e9b872;
                --plyr-video-background: #08090b;
                --plyr-video-control-background: rgba(8, 9, 11, 0.8);
                --plyr-control-color: #f2ede3;
                --plyr-control-icon-color: #f2ede3;
                --plyr-progress-loading-background: rgba(242, 237, 227, 0.25);
                --plyr-progress-playing-background: #e9b872;
                --plyr-progress-buffered-background: rgba(242, 237, 227, 0.5);
              }
              .plyr__progress__buffer {
                color: rgba(242, 237, 227, 0.5);
              }
              .plyr__progress--played {
                color: #e9b872;
              }
              .plyr__progress {
                background: rgba(255, 255, 255, 0.2) !important;
              }
              .plyr__progress__input[type="range"] {
                background: rgba(255, 255, 255, 0.2) !important;
              }
              .plyr__controls__item.plyr__controls__item--captions,
              .plyr__controls__item.plyr__controls__item--audio {
                display: none !important;
              }
              .plyr__caption {
                display: none !important;
              }
              video::-webkit-media-text-track-container {
                display: none !important;
              }
            `}</style>

            {/* Video Player */}
            {sources.length > 0 && (
              <div className="plyr-wrapper" style={{ display: 'block', lineHeight: 0, fontSize: 0 }}>
                <Plyr ref={playerRef} {...plyrProps} />
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
