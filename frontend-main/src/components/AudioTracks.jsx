import React from "react";
import { motion } from "framer-motion";
import { HiSpeakerWave } from "react-icons/hi2";
import { HiVolumeUp } from "react-icons/hi";

const LANG_MAP = {
  te: "Telugu", telugu: "Telugu",
  ta: "Tamil", tamil: "Tamil",
  hi: "Hindi", hindi: "Hindi",
  en: "English", english: "English",
  ml: "Malayalam", malayalam: "Malayalam",
  kn: "Kannada", kannada: "Kannada",
  bn: "Bengali", bengali: "Bengali",
  mr: "Marathi", marathi: "Marathi",
  pa: "Punjabi", punjabi: "Punjabi",
  ur: "Urdu", urdu: "Urdu",
  es: "Spanish", spanish: "Spanish",
  ja: "Japanese", japanese: "Japanese",
  ko: "Korean", korean: "Korean",
};

export default function AudioTracks({ audioTracks = [] }) {
  const rawTracks = Array.isArray(audioTracks) ? audioTracks : [];
  
  if (!rawTracks.length) return null;

  // Format tracks cleanly
  const tracks = rawTracks.map((track, idx) => {
    if (typeof track === "string") {
      const code = track.toLowerCase().trim();
      const langName = LANG_MAP[code] || (track.charAt(0).toUpperCase() + track.slice(1));
      return {
        title: langName,
        language: langName,
        is_default: idx === 0,
      };
    }
    const rawLang = track.language || track.title || `Track ${idx + 1}`;
    const code = rawLang.toLowerCase().trim();
    const langName = LANG_MAP[code] || (rawLang.charAt(0).toUpperCase() + rawLang.slice(1));
    return {
      ...track,
      title: track.title || langName,
      language: langName,
      is_default: track.is_default || idx === 0,
    };
  });

  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="mt-4 mb-2 p-3.5 sm:p-4 rounded-[4px] bg-bone/[0.03] border border-bone/10 backdrop-blur-md space-y-2.5"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-[3px] bg-projector/10 border border-projector/25 text-projector">
            <HiSpeakerWave className="text-sm" />
          </div>
          <h3 className="text-xs sm:text-sm font-bold text-bone tracking-wide">
            Audio Tracks
          </h3>
        </div>
        <span className="px-2 py-0.5 rounded-[2px] bg-projector/10 border border-projector/25 text-projector text-[10px] font-bold timecode">
          {tracks.length} available
        </span>
      </div>

      <div className="flex flex-wrap gap-2 pt-0.5">
        {tracks.map((track, index) => (
          <motion.div
            key={index}
            initial={{ opacity: 0, scale: 0.92 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: index * 0.04 }}
            className={`
              flex items-center gap-2 px-3 py-1.5 rounded-[3px] border text-xs transition-colors duration-200
              ${track.is_default
                ? "bg-projector/12 border-projector/40 text-bone shadow-[0_2px_8px_rgba(233,184,114,0.18)]"
                : "bg-bone/[0.05] border-bone/10 text-bone/80 hover:bg-bone/[0.08] hover:border-bone/20"
              }
            `}
          >
            <HiVolumeUp className={track.is_default ? "text-projector text-xs" : "text-bone/60 text-xs"} />
            <span className="font-semibold tracking-wide">{track.title || track.language}</span>
            {track.codec && (
              <span className="px-1.5 py-0.5 rounded-[2px] bg-ink/50 text-bone/50 text-[10px] uppercase font-mono">
                {track.codec}
              </span>
            )}
            {track.channels && (
              <span className="px-1.5 py-0.5 rounded-[2px] bg-ink/50 text-bone/50 text-[10px] font-mono">
                {track.channels === 6 ? "5.1ch" : track.channels === 8 ? "7.1ch" : `${track.channels}ch`}
              </span>
            )}
            {track.is_default && (
              <span className="px-1.5 py-0.5 rounded-[2px] bg-projector text-ink text-[10px] font-extrabold tracking-wider">
                DEFAULT
              </span>
            )}
          </motion.div>
        ))}
      </div>
    </motion.section>
  );
}
