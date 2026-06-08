import Plyr from "plyr-react";
import "plyr-react/plyr.css";
import { useEffect, useState, useRef, useMemo, forwardRef } from "react";
import { BASE_URL as BASE } from "../config";

const InlinePlayer = forwardRef(function InlinePlayer({ sources, poster, fileId, fileName }, ref) {
  const [audioTracks, setAudioTracks] = useState([]);
  const [currentAudioTrack, setCurrentAudioTrack] = useState(0);
  const [subtitleTracks, setSubtitleTracks] = useState([]);
  const [currentSubtitleTrack, setCurrentSubtitleTrack] = useState(-1);
  const [showAudioDropdown, setShowAudioDropdown] = useState(false);
  const [showSubDropdown, setShowSubDropdown] = useState(false);

  const playerRef = useRef(null);
  const audioDropdownRef = useRef(null);
  const subDropdownRef = useRef(null);

  // Expose scrollIntoView on the wrapper ref
  useEffect(() => {
    if (ref) {
      ref.current = {
        scrollIntoView: (opts) => {
          const el = playerRef.current?.plyr?.media;
          if (el) {
            el.scrollIntoView(opts);
          }
        },
      };
    }
  }, [ref]);

  // Reset tracks when sources change
  useEffect(() => {
    setAudioTracks([]);
    setCurrentAudioTrack(0);
    setSubtitleTracks([]);
    setCurrentSubtitleTrack(-1);
    setShowAudioDropdown(false);
    setShowSubDropdown(false);
  }, [sources]);

  // Fetch media info (audio + subtitles) when video loads
  useEffect(() => {
    const fetchMediaInfo = async () => {
      if (!sources.length || !fileId || !fileName) return;

      try {
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
          allSubs.push(...data.subtitle_tracks.map((t) => ({ ...t, type: "embedded" })));
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
              type: "external",
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
  }, [sources, fileId, fileName]);

  // Build source URL with audio/subtitle track params
  const buildTrackUrl = (audioIdx, subIdx) => {
    if (!fileId || !fileName) return "";
    const params = new URLSearchParams();
    params.set("audio", audioIdx);
    if (subIdx >= 0) params.set("subtitle", subIdx);
    return `${BASE}/dl/${fileId}/${encodeURIComponent(fileName)}?${params.toString()}`;
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
    if (selected && selected.type === "external") {
      const video = plyr.media;
      const existingLabel = selected.title || selected.language.toUpperCase();
      let found = false;
      for (let i = 0; i < video.textTracks.length; i++) {
        if (video.textTracks[i].label === existingLabel) {
          video.textTracks[i].mode = "showing";
          found = true;
          break;
        }
      }
      if (!found) {
        const track = document.createElement("track");
        track.kind = "subtitles";
        track.label = existingLabel;
        track.srclang = selected.language || "und";
        track.src = `${BASE}/api/subtitle/${selected.file_id}?name=${encodeURIComponent(selected.file_name || "")}`;
        track.default = false;
        video.appendChild(track);
        setTimeout(() => {
          const added = video.textTracks[video.textTracks.length - 1];
          if (added) added.mode = "showing";
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

  const plyrProps = useMemo(
    () => ({
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
    }),
    [sources, poster]
  );

  const hasToolbar = audioTracks.length > 1 || subtitleTracks.length > 0;

  return (
    <div className="w-full bg-black rounded-2xl overflow-hidden">
      {/* Toolbar: Audio + Subtitle Selectors */}
      {hasToolbar && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-[#111] border-b border-white/5">
          {/* Audio Selector */}
          {audioTracks.length > 1 && (
            <div ref={audioDropdownRef} className="relative">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowAudioDropdown((p) => !p);
                  setShowSubDropdown(false);
                }}
                className="flex items-center gap-2 text-[13px] text-white/80 hover:text-white bg-white/5 hover:bg-white/10 px-3 py-1.5 rounded transition-colors"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                  <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                  <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
                </svg>
                <span>
                  Audio:{" "}
                  {audioTracks[currentAudioTrack]?.title ||
                    audioTracks[currentAudioTrack]?.language?.toUpperCase() ||
                    `Track ${currentAudioTrack + 1}`}
                </span>
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
                      onClick={(e) => {
                        e.stopPropagation();
                        switchAudioTrack(index);
                      }}
                      className={`w-full text-left px-3 py-2.5 text-[13px] flex items-center gap-3 hover:bg-white/10 transition-colors ${index === currentAudioTrack ? "text-green-400" : "text-white/80"}`}
                    >
                      {index === currentAudioTrack ? (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
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
                onClick={(e) => {
                  e.stopPropagation();
                  setShowSubDropdown((p) => !p);
                  setShowAudioDropdown(false);
                }}
                className="flex items-center gap-2 text-[13px] text-white/80 hover:text-white bg-white/5 hover:bg-white/10 px-3 py-1.5 rounded transition-colors"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <span>
                  {currentSubtitleTrack >= 0
                    ? subtitleTracks[currentSubtitleTrack]?.title ||
                      subtitleTracks[currentSubtitleTrack]?.language?.toUpperCase() ||
                      "On"
                    : "CC"}
                </span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={`transition-transform ${showSubDropdown ? "rotate-180" : ""}`}>
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>
              {showSubDropdown && (
                <div className="absolute top-full left-0 mt-1 bg-[#1a1a1a] rounded-lg overflow-hidden min-w-[200px] border border-white/10 shadow-2xl z-50">
                  <div className="px-3 py-2 text-[11px] text-white/40 uppercase tracking-wider border-b border-white/5">Subtitles</div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      switchSubtitleTrack(-1);
                    }}
                    className={`w-full text-left px-3 py-2.5 text-[13px] flex items-center gap-3 hover:bg-white/10 transition-colors ${currentSubtitleTrack === -1 ? "text-green-400" : "text-white/80"}`}
                  >
                    {currentSubtitleTrack === -1 ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : (
                      <span className="w-[14px]" />
                    )}
                    Off
                  </button>
                  {subtitleTracks.map((track, index) => (
                    <button
                      key={index}
                      onClick={(e) => {
                        e.stopPropagation();
                        switchSubtitleTrack(index);
                      }}
                      className={`w-full text-left px-3 py-2.5 text-[13px] flex items-center gap-3 hover:bg-white/10 transition-colors ${index === currentSubtitleTrack ? "text-green-400" : "text-white/80"}`}
                    >
                      {index === currentSubtitleTrack ? (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : (
                        <span className="w-[14px]" />
                      )}
                      <span>{track.title || track.language?.toUpperCase() || `Sub ${index + 1}`}</span>
                      {track.is_default && (
                        <span className="ml-auto text-[10px] bg-green-500/20 text-green-400 px-1.5 rounded">Default</span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Remux active indicator */}
      {(currentAudioTrack > 0 || currentSubtitleTrack >= 0) && (
        <div className="px-4 py-1.5 bg-amber-500/10 border-b border-amber-500/20 flex items-center justify-between">
          <span className="text-[11px] text-amber-400/80">
            Audio/Subtitle remux active — first load may take a moment, then cached for instant switching
          </span>
        </div>
      )}

      {/* Video Player */}
      {sources.length > 0 && (
        <div className="plyr-wrapper">
          <Plyr ref={playerRef} {...plyrProps} />
        </div>
      )}
    </div>
  );
});

export default InlinePlayer;
