import { useState, useRef, useEffect } from "react";
import { FaClosedCaptioning, FaCheck } from "react-icons/fa";

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
  return `Subtitle ${track.index + 1}`;
}

export default function SubtitleSelector({ tracks, currentTrack, onTrackChange, isVisible = true }) {
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

  if (!isVisible || !tracks || tracks.length === 0) {
    return null;
  }

  const currentTrackData = currentTrack >= 0 ? tracks[currentTrack] : null;
  const currentFlag = currentTrackData ? getLanguageFlag(currentTrackData?.language, currentTrackData?.title) : "";
  const currentTitle = currentTrackData ? getDisplayTitle(currentTrackData) : "CC";

  const handleTrackSelect = (index) => {
    onTrackChange(index);
    setIsOpen(false);
  };

  return (
    <div ref={dropdownRef} className="subtitle-selector">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="subtitle-track-button"
        title="Select Subtitle Language"
      >
        <FaClosedCaptioning className="subtitle-icon" />
        {currentTrack >= 0 && <span className="lang-flag">{currentFlag}</span>}
        <span className="subtitle-title">{currentTrack >= 0 ? currentTitle : "CC"}</span>
        <span className={`dropdown-arrow ${isOpen ? "open" : ""}`}>▼</span>
      </button>

      {isOpen && (
        <div className="subtitle-track-dropdown">
          {/* Off option */}
          <button
            onClick={() => handleTrackSelect(-1)}
            className={`subtitle-track-option ${currentTrack === -1 ? "selected" : ""}`}
          >
            <span className="lang-flag">🚫</span>
            <span className="option-title">Off</span>
            {currentTrack === -1 && <FaCheck className="check-icon" />}
          </button>
          
          <div className="dropdown-divider" />
          
          {tracks.map((track, index) => {
            const flag = getLanguageFlag(track.language, track.title);
            const title = getDisplayTitle(track);
            const isSelected = index === currentTrack;
            const isForced = track.is_forced;
            const isText = track.is_text;

            return (
              <button
                key={index}
                onClick={() => handleTrackSelect(index)}
                className={`subtitle-track-option ${isSelected ? "selected" : ""}`}
              >
                <span className="lang-flag">{flag}</span>
                <span className="option-title">{title}</span>
                {isForced && <span className="forced-badge">Forced</span>}
                {!isText && <span className="format-badge">IMG</span>}
                {isSelected && <FaCheck className="check-icon" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
