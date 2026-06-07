import { AiOutlineClose } from "react-icons/ai";
import { motion, AnimatePresence } from "framer-motion";
import Plyr from "plyr-react";
import "plyr-react/plyr.css";
import { useEffect, useState, useRef, useMemo } from "react";
import { useLocation } from "react-router-dom";
import { BASE_URL as BASE } from "../config";

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
        if (props.popUpType === "movie" && props.id.telegram?.length > 0) {
          fileId = props.id.telegram[0].id;
          fileName = props.id.telegram[0].name;
        } else if (props.popUpType === "episode") {
          const season = props.id.seasons?.find((s) => s.season_number === props.seasonNumber);
          const episode = season?.episodes?.find((ep) => ep.episode_number === props.episodeNumber);
          if (episode?.telegram?.length > 0) {
            fileId = episode.telegram[0].id;
            fileName = episode.telegram[0].name;
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
            if (props.id?.telegram?.length > 0) {
              videoSources = props.id.telegram.map((q) => ({
                src: `${BASE}/dl/${q.id}/${q.name}`,
                type: "video/mp4",
                size: parseInt(q.quality.replace("p", ""), 10),
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
              if (episode?.telegram?.length > 0) {
                videoSources = episode.telegram.map((q) => ({
                  src: `${BASE}/dl/${q.id}/${q.name}`,
                  type: "video/mp4",
                  size: parseInt(q.quality.replace("p", ""), 10),
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
        "mute",
        "volume",
        "settings",
        "pip",
        "fullscreen",
      ],
      tooltips: { controls: true, seek: true },
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
              : "inset-0 w-full h-screen bg-black/90 backdrop-blur-md flex items-center justify-center"
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
              className={`text-white text-lg bg-black/50 hover:bg-black/80 rounded-full ${isMiniPlayer ? "p-1.5" : "p-2"}`}
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
            className={`flex flex-col shadow-lg relative bg-black ${
              isMiniPlayer ? "w-full rounded-lg" : "w-full max-w-5xl rounded-lg"
            }`}
          >
            {/* Toolbar: Audio + Subtitle Selectors */}
            {(audioTracks.length > 1 || subtitleTracks.length > 0) && (
              <div className="flex items-center gap-3 px-4 py-2.5 bg-[#111] border-b border-white/5">
                {/* Audio Selector */}
                {audioTracks.length > 1 && (
                  <div ref={audioDropdownRef} className="relative">
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowAudioDropdown((p) => !p); setShowSubDropdown(false); }}
                      className="flex items-center gap-2 text-[13px] text-white/80 hover:text-white bg-white/5 hover:bg-white/10 px-3 py-1.5 rounded transition-colors"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                        <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                        <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
                      </svg>
                      <span>Audio: {audioTracks[currentAudioTrack]?.title || audioTracks[currentAudioTrack]?.language?.toUpperCase() || `Track ${currentAudioTrack + 1}`}</span>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={`transition-transform ${showAudioDropdown ? "rotate-180" : ""}`}>
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </button>
                    {showAudioDropdown && (
                      <div className="absolute top-full left-0 mt-1 bg-[#1a1a1a] rounded-lg overflow-hidden min-w-[220px] border border-white/10 shadow-2xl z-50">
                        <div className="px-3 py-2 text-[11px] text-white/40 uppercase tracking-wider border-b border-white/5">Audio Track</div>
                        {audioTracks.map((track, index) => (
                          <button
                            key={index}
                            onClick={(e) => { e.stopPropagation(); switchAudioTrack(index); }}
                            className={`w-full text-left px-3 py-2.5 text-[13px] flex items-center gap-3 hover:bg-white/10 transition-colors ${index === currentAudioTrack ? "text-green-400" : "text-white/80"}`}
                          >
                            {index === currentAudioTrack ? (
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                            ) : (
                              <span className="w-[14px]" />
                            )}
                            <span>{track.title || track.language?.toUpperCase() || `Track ${index + 1}`}</span>
                            {track.channels > 2 && (
                              <span className="ml-auto text-[10px] bg-white/10 px-1.5 py-0.5 rounded text-white/50">{track.channels}ch</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Subtitle Selector */}
                {subtitleTracks.length > 0 && (
                  <div ref={subDropdownRef} className="relative">
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowSubDropdown((p) => !p); setShowAudioDropdown(false); }}
                      className="flex items-center gap-2 text-[13px] text-white/80 hover:text-white bg-white/5 hover:bg-white/10 px-3 py-1.5 rounded transition-colors"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                      </svg>
                      <span>{currentSubtitleTrack >= 0 ? (subtitleTracks[currentSubtitleTrack]?.title || subtitleTracks[currentSubtitleTrack]?.language?.toUpperCase() || 'On') : 'CC'}</span>
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={`transition-transform ${showSubDropdown ? "rotate-180" : ""}`}>
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </button>
                    {showSubDropdown && (
                      <div className="absolute top-full left-0 mt-1 bg-[#1a1a1a] rounded-lg overflow-hidden min-w-[200px] border border-white/10 shadow-2xl z-50">
                        <div className="px-3 py-2 text-[11px] text-white/40 uppercase tracking-wider border-b border-white/5">Subtitles</div>
                        <button
                          onClick={(e) => { e.stopPropagation(); switchSubtitleTrack(-1); }}
                          className={`w-full text-left px-3 py-2.5 text-[13px] flex items-center gap-3 hover:bg-white/10 transition-colors ${currentSubtitleTrack === -1 ? "text-green-400" : "text-white/80"}`}
                        >
                          {currentSubtitleTrack === -1 ? (
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                          ) : (
                            <span className="w-[14px]" />
                          )}
                          Off
                        </button>
                        {subtitleTracks.map((track, index) => (
                          <button
                            key={index}
                            onClick={(e) => { e.stopPropagation(); switchSubtitleTrack(index); }}
                            className={`w-full text-left px-3 py-2.5 text-[13px] flex items-center gap-3 hover:bg-white/10 transition-colors ${index === currentSubtitleTrack ? "text-green-400" : "text-white/80"}`}
                          >
                            {index === currentSubtitleTrack ? (
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>
                            ) : (
                              <span className="w-[14px]" />
                            )}
                            <span>{track.title || track.language?.toUpperCase() || `Sub ${index + 1}`}</span>
                            {track.is_default && <span className="ml-auto text-[10px] bg-green-500/20 text-green-400 px-1.5 rounded">Default</span>}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Seeking disabled warning */}
            {(currentAudioTrack > 0 || currentSubtitleTrack >= 0) && (
              <div className="px-4 py-1.5 bg-amber-500/10 border-b border-amber-500/20">
                <span className="text-[11px] text-amber-400/80">
                  Audio/Subtitle active — seeking/scrubbing is disabled during remuxed playback
                </span>
              </div>
            )}

            {/* Video Player */}
            {sources.length > 0 && (
              <div className="plyr-wrapper">
                <Plyr ref={playerRef} {...plyrProps} />
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
