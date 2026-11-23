import React, { useState } from "react";
import { Download, Play, MonitorPlay, Send, FileText, ChevronDown, Check } from "lucide-react";
import { BASE_URL as BASE, BOT_USERNAME } from "../config";

export default function QualityTab({
  qualities,
  selectedQuality,
  setSelectedQuality,
  telegramByQuality,
  tmdbId,
  seasonNumber,
  episodeNumber,
  db_index,
}) {
  const botUsername = BOT_USERNAME;
  const cleanBotUsername = botUsername?.replace(/^@/, "");

  const isAndroid = /android/i.test(navigator.userAgent);
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

  /*
   * "Watch" used to link to /watch/movie/... and /watch/series/... — routes the
   * router never defined, so the most prominent button on every file card landed
   * on the 404 page. The player already lives above the episode list, so the
   * button now scrolls to it and starts the selected quality instead.
   */
  const focusInlinePlayer = () => {
    const el =
      document.getElementById("cx-player") ||
      document.querySelector("[data-cx-player]");
    if (!el) return false;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    return true;
  };

  const filterRealSubtitles = (subtitles) => {
    if (!subtitles || !Array.isArray(subtitles)) return [];
    return subtitles.filter((sub) => sub && sub.id && sub.name);
  };

  if (!qualities || qualities.length === 0) return null;

  return (
    <div className="space-y-3 mt-2">
      {/* Quality Selector Tabs */}
      <div
        role="group"
        aria-label="Choose a quality"
        className="flex flex-wrap gap-2"
      >
        {qualities.map((quality) => {
          const active = selectedQuality === quality;
          return (
            <button
              key={quality}
              type="button"
              aria-pressed={active}
              onClick={() => setSelectedQuality(quality)}
              className={`
                inline-flex items-center gap-1.5 px-4 py-2 timecode text-[11px] font-semibold rounded-[3px] transition-colors duration-200 border
                ${active
                  ? "bg-projector border-projector text-ink"
                  : "bg-bone/5 border-bone/10 text-bone/60 hover:bg-bone/10 hover:text-bone hover:border-bone/20"
                }
              `}
            >
              {active && <Check className="h-3 w-3" aria-hidden="true" />}
              {quality}
            </button>
          );
        })}
      </div>

      {/* Collapse affordance: tapping a selected chip used to silently hide the
          whole file list, which felt destructive. Make hiding explicit. */}
      {selectedQuality && (
        <div className="flex items-center justify-between gap-3">
          <p className="timecode text-[10px] uppercase tracking-[0.14em] text-bone-subtle">
            {selectedQuality} ·{" "}
            {(telegramByQuality[selectedQuality] || []).length} file
            {(telegramByQuality[selectedQuality] || []).length === 1 ? "" : "s"}
          </p>
          <button
            type="button"
            onClick={() => setSelectedQuality("")}
            className="timecode text-[10px] uppercase tracking-[0.14em] text-projector/80 hover:text-projector transition-colors"
          >
            Hide files
          </button>
        </div>
      )}

      {/* Files for selected quality */}
      {selectedQuality && telegramByQuality[selectedQuality]?.map((file, index) => {
        const downloadUrl = `${BASE}/dl/${file.id}/${encodeURIComponent(file.name)}`;
        const subtitles = filterRealSubtitles(file.subtitles);

        let playUrl = downloadUrl;
        if (isAndroid) {
          playUrl = `intent:${downloadUrl}#Intent;type=video/x-matroska;action=android.intent.action.VIEW;end;`;
        } else if (isIOS) {
          playUrl = `vlc://${downloadUrl}`;
        }

        const telegramUrl = cleanBotUsername
          ? seasonNumber
            ? `https://t.me/${cleanBotUsername}?start=file_${tmdbId}_${db_index}_${seasonNumber}_${episodeNumber}_${selectedQuality}`
            : `https://t.me/${cleanBotUsername}?start=file_${tmdbId}_${db_index}_${selectedQuality}`
          : null;

        return (
          <div
            key={index}
            className="bg-bone/[0.03] border border-bone/8 rounded-[4px] p-3 sm:p-4 space-y-3"
          >
            {/* File info row */}
            <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2">
              <span className="text-sm font-medium text-bone/80 truncate max-w-xs">
                {file.name}
              </span>
              <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                {file.size && (
                  <span className="px-2 py-1 timecode text-[10px] font-semibold bg-bone/5 border border-bone/10 text-bone/50 rounded-[2px]">
                    {file.size}
                  </span>
                )}
                <span className="px-2 py-1 timecode text-[10px] font-bold border border-projector/30 text-projector rounded-[2px]">
                  {selectedQuality}
                </span>
                {subtitles.length > 0 && (
                  <span className="px-2 py-1 timecode text-[10px] font-semibold border border-projector/30 text-projector rounded-[2px]">
                    {subtitles.length} sub{subtitles.length > 1 ? "s" : ""}
                  </span>
                )}
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  if (!focusInlinePlayer()) {
                    // No inline player mounted (e.g. a movie page without a
                    // source): fall back to the direct stream rather than a
                    // dead /watch route.
                    window.location.href = downloadUrl;
                  }
                }}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold bg-projector text-ink rounded-[3px] hover:bg-projector-bright transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
                title="Watch online"
              >
                <MonitorPlay className="w-3.5 h-3.5" />
                Watch
              </button>
              <a
                href={downloadUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold bg-bone/8 border border-bone/12 text-bone/70 rounded-[3px] hover:bg-bone/14 hover:text-bone transition-colors"
                title="Download video"
              >
                <Download className="w-3.5 h-3.5" />
                Download
              </a>
              <a
                href={playUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold bg-bone/8 border border-bone/12 text-bone/70 rounded-[3px] hover:bg-bone/14 hover:text-bone transition-colors"
                title="External Player"
              >
                <Play className="w-3.5 h-3.5" />
                Player
              </a>
              {telegramUrl && (
                <a
                  href={telegramUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold bg-[#229ED9]/10 border border-[#229ED9]/20 text-[#4FB3E5] rounded-[3px] hover:bg-[#229ED9]/20 transition-colors"
                  title="Get on Telegram"
                >
                  <Send className="w-3.5 h-3.5" />
                  Telegram
                </a>
              )}
            </div>

            {/* Subtitles dropdown */}
            {subtitles.length > 0 && (
              <details className="group rounded-[4px] border border-bone/8 bg-bone/[0.03] overflow-hidden">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-xs font-semibold text-bone/60 hover:text-bone hover:bg-bone/5 transition-colors select-none [&::-webkit-details-marker]:hidden">
                  <span className="flex items-center gap-2">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-projector" />
                    Subtitles ({subtitles.length})
                  </span>
                  <ChevronDown className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180 text-bone/55" />
                </summary>

                <div className="max-h-48 overflow-y-auto border-t border-bone/8 p-2 space-y-1.5">
                  {subtitles.map((subtitle, subtitleIndex) => {
                    const subtitleUrl = `${BASE}/dl/${subtitle.id}/${encodeURIComponent(subtitle.name)}`;
                    const label = subtitle.label || subtitle.language || "Subtitle";
                    return (
                      <div
                        key={`${subtitle.id}-${subtitleIndex}`}
                        className="flex items-center justify-between gap-2 rounded-[3px] bg-bone/5 px-3 py-2"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium text-bone/80">
                            {subtitle.name}
                          </p>
                          <p className="truncate text-[10px] text-bone/60">
                            {label}{subtitle.size ? ` · ${subtitle.size}` : ""}
                          </p>
                        </div>
                        <a
                          href={subtitleUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center justify-center w-8 h-8 rounded-[3px] border border-bone/10 text-bone/50 hover:bg-bone/10 hover:text-bone transition-colors flex-shrink-0"
                          title="Download subtitle"
                        >
                          <Download className="h-3.5 w-3.5" />
                        </a>
                      </div>
                    );
                  })}
                </div>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}
