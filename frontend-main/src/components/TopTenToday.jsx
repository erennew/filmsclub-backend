import React from "react";
import { Link } from "react-router-dom";
import { LazyLoadImage } from "react-lazy-load-image-component";
import useReducedMotion from "../hooks/useReducedMotion";
import "react-lazy-load-image-component/src/effects/black-and-white.css";
import { BsPlayFill } from "react-icons/bs";
import posterPlaceholder from "../assets/images/poster-placeholder.png";
import { posterUrl } from "../utils/imageUrl";
import WatchlistButton from "./WatchlistButton";

// Timecode-style rank numbers for the Top 10 effect
const RankNumber = ({ rank }) => {
  const top = rank <= 3;
  return (
    <span
      className={`
        absolute -bottom-3 -left-3 z-10 select-none pointer-events-none
        font-mono font-semibold leading-none
        text-[3.8rem] sm:text-[4.6rem]
        ${top ? "text-projector" : "text-bone/50"}
      `}
      style={{
        WebkitTextStroke: top ? "1.5px rgba(233,184,114,0.45)" : "1.5px rgba(242,237,227,0.10)",
        WebkitTextFillColor: top ? "rgba(233,184,114,0.10)" : "rgba(242,237,227,0.04)",
        textShadow: "none",
      }}
    >
      {String(rank).padStart(2, "0")}
    </span>
  );
};

const TopTenToday = ({ data, isLoading, sectionTitle }) => {
  const reduceMotion = useReducedMotion();

  if (isLoading) {
    return (
      <section className="mt-10 md:mt-14">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-px h-5 bg-projector shrink-0" />
          <h2 className="section-title text-base sm:text-lg md:text-xl tracking-tight">
            {sectionTitle}
          </h2>
        </div>
        <div className="flex gap-5 sm:gap-6 overflow-x-auto pb-4 no-scrollbar">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="relative flex-shrink-0 w-[100px] sm:w-[130px]">
              <div className="aspect-[2/3] rounded-[4px] bg-carbon skeleton" />
              <div className="mt-2 h-3 w-3/4 rounded-[2px] bg-carbon skeleton" />
              {/* rank placeholder */}
              <div className="absolute -bottom-2 -left-2 w-10 h-16 bg-carbon skeleton rounded-[3px]" />
            </div>
          ))}
        </div>
      </section>
    );
  }

  if (!data || data.length === 0) return null;

  return (
    <section className="mt-10 md:mt-14">
      {/* Section Header */}
      <div className="flex items-center justify-between mb-5 gap-4">
        <div className="flex items-center gap-3">
          <div className="w-px h-5 bg-projector shrink-0" />
          <h2 className="section-title text-base sm:text-lg md:text-xl tracking-tight">
            {sectionTitle}
          </h2>
        </div>
      </div>

      {/* Horizontal Ranked Cards */}
      <div className="flex gap-5 sm:gap-7 overflow-x-auto pb-6 snap-x snap-mandatory no-scrollbar">
        {data.map((item, index) => {
          const rank = index + 1;
          return (
            <div
              key={item.tmdb_id || index}
              /* Fade-up reveal via CSS, staggered by index. Skipped entirely
                 under reduced motion. Mirrors the old framer tween:
                 opacity 0→1, y 12→0, delay min(index * 0.05, 0.5). */
              className={`relative flex-shrink-0 snap-start${
                reduceMotion ? "" : " animate-reveal-up"
              }`}
              style={{
                paddingLeft: "18px",
                paddingBottom: "16px",
                animationDelay: reduceMotion
                  ? undefined
                  : `${Math.min(index * 0.05, 0.5)}s`,
              }}
            >
              {/* Big rank number behind card */}
              <RankNumber rank={rank} />

              <div className="group relative block w-[100px] sm:w-[128px] md:w-[140px]">
                <Link
                  to={
                    item.media_type === "movie"
                      ? `/mov/${item.tmdb_id}`
                      : `/ser/${item.tmdb_id}`
                  }
                  aria-label={`${item.title} — ${item.media_type === "movie" ? "Movie" : "Series"}`}
                  className="absolute inset-0 z-10 rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
                />

                {/* Poster */}
                <div className="relative aspect-[2/3] overflow-hidden rounded-[4px] bg-carbon group-hover:shadow-[0_18px_40px_-18px_rgba(0,0,0,0.9)] transition-shadow duration-300">
                  <LazyLoadImage
                    src={posterUrl(item.poster || posterPlaceholder)}
                    effect="black-and-white"
                    alt={item.title}
                    /* Without wrapperClassName the placeholder/loading span
                       collapses to intrinsic size, so these posters rendered
                       letterboxed instead of filling the 2:3 frame like the
                       ones in MovieCard. */
                    wrapperClassName="absolute inset-0 w-full h-full"
                    className="w-full h-full object-cover object-center transition-transform duration-500 group-hover:scale-105"
                  />

                  {/* Play overlay */}
                  <div className="absolute inset-0 bg-ink/55 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center rounded-[4px]">
                    <div className="w-11 h-11 rounded-[3px] bg-projector text-ink flex items-center justify-center shadow-[0_8px_20px_-8px_rgba(233,184,114,0.7)]">
                      <BsPlayFill className="text-lg ml-0.5" />
                    </div>
                  </div>

                  {/* Ring on hover */}
                  <div className="absolute inset-0 rounded-[4px] ring-1 ring-inset ring-bone/10 group-hover:ring-projector/60 transition-all duration-300" />

                  <div className="absolute bottom-1 right-1 z-20">
                    <WatchlistButton item={item} size="sm" />
                  </div>
                </div>

                {/* Title */}
                <div className="mt-2 pl-0.5">
                  <p className="line-clamp-2 text-[11px] sm:text-xs font-semibold text-bone leading-tight group-hover:text-projector transition-colors duration-200">
                    {item.title}
                  </p>
                  <div className="flex items-center gap-1.5 mt-1">
                    {item.release_year && (
                      <span className="timecode text-[10px] text-bone/55">{item.release_year}</span>
                    )}
                    <span className="font-mono text-[10px] uppercase tracking-widest text-bone/55">
                      {item.media_type === "movie" ? "Movie" : "Series"}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};

export default TopTenToday;
