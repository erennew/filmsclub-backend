import React, { useEffect, useMemo, useState } from "react";
import { CalendarClock, Timer } from "lucide-react";

const formatDate = (airDate) => {
  if (!airDate) return "Date not announced";
  const date = new Date(`${airDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) return airDate;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
};

const getCountdown = (airDate, now) => {
  if (!airDate) return null;
  const target = new Date(`${airDate}T00:00:00`).getTime();
  if (Number.isNaN(target)) return null;
  const diff = target - now;
  if (diff <= 0) return "Available soon";
  const days = Math.floor(diff / 86_400_000);
  const hours = Math.floor((diff % 86_400_000) / 3_600_000);
  const minutes = Math.floor((diff % 3_600_000) / 60_000);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${Math.max(1, minutes)}m`;
};

export default function UpcomingEpisodeCard({ episode }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  const countdown = useMemo(() => getCountdown(episode?.air_date, now), [episode?.air_date, now]);

  if (!episode?.air_date) return null;

  return (
    <div className="mt-6 rounded-[4px] border border-projector/20 bg-gradient-to-r from-projector/10 via-projector/[0.03] to-transparent p-4 md:p-5">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          {/* Badge */}
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="inline-flex items-center gap-1.5 px-2 py-1 timecode text-[10px] font-bold rounded-[2px] border border-projector/30 bg-projector/10 text-projector uppercase tracking-[0.1em]">
              <CalendarClock className="h-3 w-3" />
              Upcoming Episode
            </span>
            <span className="text-xs text-bone/50">{formatDate(episode.air_date)}</span>
          </div>
          {/* Title */}
          <h3 className="truncate text-sm sm:text-base font-semibold text-bone">
            S{episode.season_number || "?"}E{episode.episode_number || "?"} · {episode.name || "Next episode"}
          </h3>
          {episode.overview && (
            <p className="mt-1 text-xs text-bone/50 line-clamp-2">{episode.overview}</p>
          )}
        </div>

        {/* Countdown Badge */}
        {countdown && (
          <div className="flex-shrink-0 flex flex-col items-center justify-center rounded-[3px] border border-bone/10 bg-bone/5 px-3 py-2 text-center min-w-[4rem]">
            <div className="flex items-center justify-center gap-1 text-[10px] text-bone/60 mb-0.5">
              <Timer className="h-3 w-3" />
              <span>in</span>
            </div>
            <p className="timecode text-sm font-bold text-bone">{countdown}</p>
          </div>
        )}
      </div>
    </div>
  );
}
