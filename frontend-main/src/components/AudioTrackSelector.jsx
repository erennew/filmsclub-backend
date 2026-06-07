import { useState, useRef, useEffect } from "react";
import { FaVolumeUp, FaCheck } from "react-icons/fa";

// Language code to flag emoji mapping
const LANG_FLAGS = {
  eng: "🇬🇧", en: "🇬🇧", english: "🇬🇧",
  hin: "🇮🇳", hi: "🇮🇳", hindi: "🇮🇳",
  tam: "🇮🇳", ta: "🇮🇳", tamil: "🇮🇳",
  tel: "🇮🇳", te: "🇮🇳", telugu: "🇮🇳",
  kan: "🇮🇳", kn: "🇮🇳", kannada: "🇮🇳",
  mal: "🇮🇳", ml: "🇮🇳", malayalam: "🇮🇳",
  mar: "🇮🇳", mr: "🇮🇳", marathi: "🇮🇳",
  ben: "🇧🇩", bn: "🇧🇩", bengali: "🇧🇩",
  guj: "🇮🇳", gu: "🇮🇳", gujarati: "🇮🇳",
  pun: "🇮🇳", pa: "🇮🇳", punjabi: "🇮🇳",
  urd: "🇵🇰", ur: "🇵🇰", urdu: "🇵🇰",
  ara: "🇸🇦", ar: "🇸🇦", arabic: "🇸🇦",
  spa: "🇪🇸", es: "🇪🇸", spanish: "🇪🇸",
  fra: "🇫🇷", fr: "🇫🇷", french: "🇫🇷",
  deu: "🇩🇪", de: "🇩🇪", german: "🇩🇪",
  ita: "🇮🇹", it: "🇮🇹", italian: "🇮🇹",
  por: "🇵🇹", pt: "🇵🇹", portuguese: "🇵🇹",
  rus: "🇷🇺", ru: "🇷🇺", russian: "🇷🇺",
  jpn: "🇯🇵", ja: "🇯🇵", japanese: "🇯🇵",
  kor: "🇰🇷", ko: "🇰🇷", korean: "🇰🇷",
  zho: "🇨🇳", zh: "🇨🇳", chinese: "🇨🇳",
  und: "🌐", unknown: "🌐"
};

function getLanguageFlag(languageCode, title) {
  if (!languageCode) return "🌐";
  const code = languageCode.toLowerCase();
  if (LANG_FLAGS[code]) return LANG_FLAGS[code];
  if (title) {
    const titleLower = title.toLowerCase();
    for (const [key, flag] of Object.entries(LANG_FLAGS)) {
      if (titleLower.includes(key)) return flag;
    }
  }
  return "🌐";
}

function getDisplayTitle(track) {
  if (track.title && track.title !== "Unknown") {
    return track.title;
  }
  if (track.language) {
    return track.language.toUpperCase();
  }
  return `Audio ${track.index + 1}`;
}

export default function AudioTrackSelector({ tracks, currentTrack, onTrackChange, isVisible = true }) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  if (!isVisible || !tracks || tracks.length <= 1) {
    return null;
  }

  const currentTrackData = tracks[currentTrack] || tracks[0];
  const currentFlag = getLanguageFlag(currentTrackData?.language, currentTrackData?.title);
  const currentTitle = getDisplayTitle(currentTrackData);

  const handleTrackSelect = (index) => {
    onTrackChange(index);
    setIsOpen(false);
  };

  return (
    <div ref={dropdownRef} className="audio-track-selector">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="audio-track-button"
        title="Select Audio Language"
      >
        <FaVolumeUp className="audio-icon" />
        <span className="lang-flag">{currentFlag}</span>
        <span className="audio-title">{currentTitle}</span>
        <span className={`dropdown-arrow ${isOpen ? "open" : ""}`}>▼</span>
      </button>

      {isOpen && (
        <div className="audio-track-dropdown">
          {tracks.map((track, index) => {
            const flag = getLanguageFlag(track.language, track.title);
            const title = getDisplayTitle(track);
            const isSelected = index === currentTrack;

            return (
              <button
                key={index}
                onClick={() => handleTrackSelect(index)}
                className={`audio-track-option ${isSelected ? "selected" : ""}`}
              >
                <span className="lang-flag">{flag}</span>
                <span className="option-title">{title}</span>
                {track.channels > 2 && (
                  <span className="channels-badge">{track.channels}ch</span>
                )}
                {isSelected && <FaCheck className="check-icon" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
