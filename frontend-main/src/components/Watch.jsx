import { AiOutlineClose } from "react-icons/ai";
import { motion, AnimatePresence } from "framer-motion";
import Plyr from "plyr-react";
import "plyr-react/plyr.css";
import { useEffect, useState, useRef } from "react";
import { useLocation } from "react-router-dom";
import axios from "axios";
import AudioTrackSelector from "./AudioTrackSelector";
import SubtitleSelector from "./SubtitleSelector";

export default function WatchTrailer(props) {
  const [sources, setSources] = useState([]);
  const [poster, setPoster] = useState("");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [audioTracks, setAudioTracks] = useState([]);
  const [currentAudioTrack, setCurrentAudioTrack] = useState(0);
  const [audioApiSupported, setAudioApiSupported] = useState(true);
  const [subtitleTracks, setSubtitleTracks] = useState([]);
  const [currentSubtitleTrack, setCurrentSubtitleTrack] = useState(-1); // -1 = off
  const BASE = import.meta.env.VITE_BASE_URL;

  const playerRef = useRef(null);
  const location = useLocation();

  // Reset audio and subtitle tracks when modal closes
  useEffect(() => {
    if (!props.isWatchMoviePopupOpen && !props.isWatchEpisodePopupOpen) {
      setAudioTracks([]);
      setCurrentAudioTrack(0);
      setSubtitleTracks([]);
      setCurrentSubtitleTrack(-1);
    }
  }, [props.isWatchMoviePopupOpen, props.isWatchEpisodePopupOpen]);

  // Fetch audio track info when video loads
  useEffect(() => {
    const fetchAudioTracks = async () => {
      if (!sources.length || !props.id) return;

      try {
        // Get the first quality's file info
        let fileId, fileName;
        
        if (props.popUpType === "movie" && props.id.telegram?.length > 0) {
          fileId = props.id.telegram[0].id;
          fileName = props.id.telegram[0].name;
        } else if (props.popUpType === "episode") {
          const season = props.id.seasons?.find(
            (s) => s.season_number === props.seasonNumber
          );
          const episode = season?.episodes?.find(
            (ep) => ep.episode_number === props.episodeNumber
          );
          if (episode?.telegram?.length > 0) {
            fileId = episode.telegram[0].id;
            fileName = episode.telegram[0].name;
          }
        }

        if (!fileId || !fileName) return;

        // Fetch audio track info from backend
        const response = await fetch(
          `${BASE}/api/media-info/${fileId}?name=${encodeURIComponent(fileName)}`
        );
        
        if (response.ok) {
          const data = await response.json();
          if (data.audio_tracks && data.audio_tracks.length > 1) {
            setAudioTracks(data.audio_tracks);
            // Find default track index
            const defaultIndex = data.audio_tracks.findIndex(t => t.is_default);
            setCurrentAudioTrack(defaultIndex >= 0 ? defaultIndex : 0);
          }
          
          // Combine embedded and external subtitles
          let allSubtitles = [];
          
          // Add embedded subtitle tracks
          if (data.subtitle_tracks && data.subtitle_tracks.length > 0) {
            allSubtitles = data.subtitle_tracks.map(t => ({
              ...t,
              type: 'embedded'
            }));
          }
          
          // Add external subtitle files
          if (data.external_subtitles && data.external_subtitles.length > 0) {
            const externalSubs = data.external_subtitles.map(s => ({
              index: allSubtitles.length,  // Continue index after embedded
              stream_index: -1,
              codec: s.format,
              language: s.language,
              title: s.label || s.language.toUpperCase(),
              is_text: true,
              is_default: s.is_default,
              is_forced: false,
              type: 'external',
              file_id: s.id,
              file_name: s.name
            }));
            allSubtitles = [...allSubtitles, ...externalSubs];
          }
          
          if (allSubtitles.length > 0) {
            setSubtitleTracks(allSubtitles);
            // Find default subtitle (forced, external default, or first)
            const defaultIndex = allSubtitles.findIndex(t => t.is_forced || t.is_default);
            setCurrentSubtitleTrack(defaultIndex >= 0 ? defaultIndex : -1);
          }
        }
      } catch (error) {
        console.error("Error fetching audio tracks:", error);
      }
    };

    // Delay slightly to let video element initialize
    const timer = setTimeout(fetchAudioTracks, 500);
    return () => clearTimeout(timer);
  }, [sources, props.id, props.popUpType, props.seasonNumber, props.episodeNumber, BASE]);

  // Switch audio track
  const switchAudioTrack = (trackIndex) => {
    const video = playerRef.current?.plyr?.media;
    if (!video || !video.audioTracks) {
      setAudioApiSupported(false);
      return;
    }

    // Switch to selected track
    for (let i = 0; i < video.audioTracks.length; i++) {
      video.audioTracks[i].enabled = (i === trackIndex);
    }
    setCurrentAudioTrack(trackIndex);
  };

  // Switch subtitle track
  const switchSubtitleTrack = (trackIndex) => {
    const video = playerRef.current?.plyr?.media;
    if (!video || !video.textTracks) {
      return;
    }

    // Get the selected track info
    const selectedTrack = subtitleTracks[trackIndex];
    
    // For external subtitles, we need to load them dynamically
    if (selectedTrack && selectedTrack.type === 'external') {
      // Create a new text track for external subtitle
      const trackUrl = `${BASE}/api/subtitle/${selectedTrack.file_id}?name=${encodeURIComponent(selectedTrack.file_name)}`;
      
      // Remove existing external tracks
      for (let i = video.textTracks.length - 1; i >= 0; i--) {
        if (video.textTracks[i].label?.startsWith('External:')) {
          video.textTracks[i].mode = 'disabled';
        }
      }
      
      // Add new track element for external subtitle
      const track = document.createElement('track');
      track.kind = 'subtitles';
      track.label = `External: ${selectedTrack.title}`;
      track.srclang = selectedTrack.language;
      track.src = trackUrl;
      track.default = true;
      video.appendChild(track);
      
      // Enable the new track
      setTimeout(() => {
        const newTrack = video.textTracks[video.textTracks.length - 1];
        if (newTrack) {
          newTrack.mode = 'showing';
        }
      }, 100);
    } else {
      // For embedded subtitles, switch native tracks
      for (let i = 0; i < video.textTracks.length; i++) {
        video.textTracks[i].mode = (i === trackIndex) ? "showing" : "hidden";
      }
    }
    
    setCurrentSubtitleTrack(trackIndex);
  };

  useEffect(() => {
    const fetchData = async () => {
      if (props.isWatchMoviePopupOpen || props.isWatchEpisodePopupOpen) {
        try {
          let videoSources = [];
          let selectedPoster = "";

          if (props.popUpType === "movie") {
            videoSources = props.id.telegram.map((q) => ({
              src: `${BASE}/dl/${q.id}/${q.name}`,
              type: "video/mp4",
              size: parseInt(q.quality.replace("p", ""), 10),
            }));
            selectedPoster = props.id.backdrop;
            // Track movie watch
            if (props.id.tmdb_id) {
              fetch(`${BASE}/api/track-view?tmdb_id=${props.id.tmdb_id}&media_type=movie`, {
                method: "POST",
                keepalive: true,
              }).catch(() => {});
            }
          } else if (props.popUpType === "episode") {
            const season = props.id.seasons.find(
              (season) => season.season_number === props.seasonNumber
            );

            if (season) {
              const episode = season.episodes.find(
                (ep) => ep.episode_number === props.episodeNumber
              );

              if (episode) {
                videoSources = episode.telegram.map((q) => ({
                  src: `${BASE}/dl/${q.id}/${q.name}`,
                  type: "video/mp4",
                  size: parseInt(q.quality.replace("p", ""), 10),
                }));
                selectedPoster = episode.episode_backdrop;
              }
            }
            // Track TV watch
            if (props.id.tmdb_id) {
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
    if (props.popUpType === "trailer") {
      props.setIsTrailerPopupOpen(false);
    } else if (props.popUpType === "movie") {
      props.setIsWatchMoviePopupOpen(false);
    } else {
      props.setIsWatchEpisodePopupOpen(false);
    }
  };

  const plyrProps = {
    source: {
      type: "video",
      sources: sources,
    },
    options: {
      poster: poster,
      settings: ["captions", "quality", "speed"],
      controls: [
        "play-large",
        "play",
        "progress",
        "current-time",
        "mute",
        "volume",
        "captions",
        "settings",
        "fullscreen",
      ],
    },
  };

  return (
    <AnimatePresence>
      {isModalOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.3 }}
          className="fixed inset-0 z-30 w-full h-screen bg-black/90 backdrop-blur-md flex items-center justify-center"
        >
          <button
            onClick={closeModal}
            className="absolute top-5 right-5 text-white text-2xl z-50"
          >
            <AiOutlineClose />
          </button>

          <motion.div
            initial={{ scale: 0.9 }}
            animate={{ scale: 1 }}
            exit={{ scale: 0.9 }}
            transition={{ duration: 0.3, ease: "easeInOut" }}
            className="w-full max-w-4xl rounded-lg overflow-hidden shadow-lg relative"
          >
            {/* Subtitle Track Selector */}
            <SubtitleSelector
              tracks={subtitleTracks}
              currentTrack={currentSubtitleTrack}
              onTrackChange={switchSubtitleTrack}
              isVisible={subtitleTracks.length > 0}
            />
            
            {/* Audio Track Selector */}
            <AudioTrackSelector
              tracks={audioTracks}
              currentTrack={currentAudioTrack}
              onTrackChange={switchAudioTrack}
              isVisible={audioTracks.length > 1}
            />
            
            {/* Firefox warning */}
            {!audioApiSupported && audioTracks.length > 1 && (
              <div className="audio-api-warning">
                Audio switching requires Chrome/Edge/Safari
              </div>
            )}
            
            <Plyr ref={playerRef} {...plyrProps} id="player" />
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
