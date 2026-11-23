import React, { useRef } from "react";
import { Link } from "react-router-dom";
import MovieCard from "./MovieCard";
import useReducedMotion from "../hooks/useReducedMotion";
import "react-lazy-load-image-component/src/effects/black-and-white.css";
import { HiArrowRight, HiChevronLeft, HiChevronRight } from "react-icons/hi2";
import MovieCardSkeleton from "./MovieCardSkeleton";

export default function HomeSection(props) {
  const scrollRef = useRef(null);
  const isDragging = useRef(false);
  const dragMoved = useRef(false);
  const startX = useRef(0);
  const scrollLeft = useRef(0);
  const reduceMotion = useReducedMotion();

  // ── Mouse drag scroll (desktop) ───────────────────────────────
  const onMouseDown = (e) => {
    isDragging.current = true;
    dragMoved.current = false;
    startX.current = e.pageX - scrollRef.current.offsetLeft;
    scrollLeft.current = scrollRef.current.scrollLeft;
    scrollRef.current.style.cursor = "grabbing";
    scrollRef.current.style.userSelect = "none";
  };

  const onMouseMove = (e) => {
    if (!isDragging.current) return;
    e.preventDefault();
    const x = e.pageX - scrollRef.current.offsetLeft;
    const walk = (x - startX.current) * 1.4;
    // Remember that this gesture moved the rail. `click` fires *after* mouseup,
    // by which point `isDragging` is already false — so the old guard on
    // `isDragging` never suppressed the navigation and releasing a drag opened
    // whichever card the cursor happened to be over.
    if (Math.abs(walk) > 6) dragMoved.current = true;
    scrollRef.current.scrollLeft = scrollLeft.current - walk;
  };

  const stopDrag = () => {
    isDragging.current = false;
    if (scrollRef.current) {
      scrollRef.current.style.cursor = "grab";
      scrollRef.current.style.removeProperty("user-select");
    }
  };

  const scrollBy = (dir) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * 320, behavior: "smooth" });
  };

  return (
    <section className="mt-10 md:mt-14">
      {/* Section Header */}
      <div className="flex items-end justify-between mb-4 sm:mb-5 gap-4 border-b border-bone/10 pb-3">
        <h2 className="section-title text-[1.3rem] sm:text-2xl md:text-[1.7rem] leading-none truncate">
          {props.sectionTitle}
        </h2>

        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => scrollBy(-1)}
            aria-label="Scroll left"
            className="hidden sm:flex items-center justify-center w-8 h-8 rounded-[3px] bg-transparent border border-bone/15 text-bone/50 hover:bg-bone/10 hover:text-bone hover:border-bone/30 transition-colors duration-200"
          >
            <HiChevronLeft className="text-sm" />
          </button>
          <button
            onClick={() => scrollBy(1)}
            aria-label="Scroll right"
            className="hidden sm:flex items-center justify-center w-8 h-8 rounded-[3px] bg-transparent border border-bone/15 text-bone/50 hover:bg-bone/10 hover:text-bone hover:border-bone/30 transition-colors duration-200"
          >
            <HiChevronRight className="text-sm" />
          </button>

          {props.sectionSeeMoreButtonLink && (
            <Link
              to={props.sectionSeeMoreButtonLink}
              className="flex items-center gap-2 ml-2 timecode text-[10px] uppercase tracking-[0.16em] text-bone/70 hover:text-projector transition-colors duration-200 group"
              style={{ textDecoration: "none" }}
            >
              <span>See all</span>
              <HiArrowRight className="text-sm group-hover:translate-x-1 transition-transform duration-200" />
            </Link>
          )}
        </div>
      </div>

      {/* Cards — horizontal drag scroll */}
      {!props.isMovieDataLoading ? (
        <div className="relative">
          <div
            ref={scrollRef}
            className="flex gap-2.5 sm:gap-3 overflow-x-auto pb-3 no-scrollbar select-none"
            style={{ cursor: "grab", scrollSnapType: "x mandatory" }}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={stopDrag}
            onMouseLeave={stopDrag}
          >
            {(props.movieData || []).map((movie, index) => (
              <div
                key={`${movie.media_type ?? "m"}:${movie.tmdb_id ?? movie._id ?? index}`}
                /* Fade-up reveal via CSS. `animate-reveal-up` is omitted for
                   reduced-motion visitors, so they get no animation at all
                   (not a shortened one). The per-card stagger lives in
                   animation-delay, matching framer's index * 0.04 (capped at
                   0.4s). */
                className={`flex-shrink-0 snap-start${
                  reduceMotion ? "" : " animate-reveal-up"
                }`}
                style={{
                  width: "clamp(118px, 18vw, 190px)",
                  animationDelay: reduceMotion
                    ? undefined
                    : `${Math.min(index * 0.04, 0.4)}s`,
                }}
                onClickCapture={(e) => {
                  // Suppress the click that ends a drag gesture; a genuine tap
                  // (no movement) still navigates.
                  if (dragMoved.current) {
                    e.preventDefault();
                    e.stopPropagation();
                    dragMoved.current = false;
                  }
                }}
              >
                <MovieCard movie={movie} />
              </div>
            ))}
          </div>
        </div>
      ) : (
        <MovieCardSkeleton count={8} horizontal />
      )}
    </section>
  );
}
