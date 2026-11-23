import React, { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Swiper, SwiperSlide } from "swiper/react";
import { LazyLoadImage } from "react-lazy-load-image-component";
import { Autoplay, A11y } from "swiper/modules";
import useReducedMotion from "../hooks/useReducedMotion";
import { BsPlayFill } from "react-icons/bs";
import { PiStarFill } from "react-icons/pi";

import "swiper/css";
import "react-lazy-load-image-component/src/effects/blur.css";
import { backdropUrl } from "../utils/imageUrl";

const HERO_H = "clamp(480px, 88svh, 760px)";
const INK = "#08090B";

function HeroSkeleton() {
  return (
    <div
      className="w-full skeleton -mx-3 sm:-mx-4 md:-mx-8 lg:-mx-12 xl:-mx-16 -mt-16 md:-mt-20"
      style={{ height: HERO_H }}
      aria-hidden="true"
    />
  );
}

export default function HeroSlider({ movieData, isMovieDataLoading, hasError }) {
  if (isMovieDataLoading) return <HeroSkeleton />;
  if (!movieData || movieData.length === 0) {
    // Never return null: an empty hero used to delete the whole top of the page,
    // so a failed request opened onto blank space with no explanation.
    return <HeroFallback hasError={hasError} />;
  }

  return <HeroCarousel movieData={movieData} />;
}

/* Keeps the hero's reserved height and gives the visitor a way forward. */
function HeroFallback({ hasError }) {
  return (
    <div
      className="relative -mx-3 sm:-mx-4 md:-mx-8 lg:-mx-12 xl:-mx-16 -mt-16 md:-mt-20 overflow-hidden bg-carbon flex items-center"
      style={{ minHeight: "clamp(360px, 60svh, 520px)" }}
    >
      <div className="w-full max-w-2xl px-5 sm:px-8 md:px-12 py-14">
        <p className="timecode text-[10px] uppercase tracking-[0.24em] text-projector/80">
          {hasError ? "Projection interrupted" : "Intermission"}
        </p>
        <h1 className="font-display text-3xl sm:text-4xl md:text-5xl text-bone mt-3 leading-tight">
          {hasError ? "Tonight's feature is late" : "The screening room is warming up"}
        </h1>
        <p className="text-sm sm:text-base text-bone/60 mt-4 leading-relaxed max-w-lg">
          {hasError
            ? "We couldn't reach the library just now. The shelves below may still load — or try again in a moment."
            : "Nothing is queued on the marquee yet. Browse the library while we roll the reel."}
        </p>
        <div className="flex flex-wrap items-center gap-2.5 mt-7">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-2 rounded-[3px] bg-projector px-5 py-2.5 min-h-[44px] text-sm font-bold text-ink transition-colors hover:bg-[#F0CA98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
          >
            Try again
          </button>
          <Link
            to="/Movies"
            className="inline-flex items-center gap-2 rounded-[3px] border border-bone/20 bg-bone/5 px-5 py-2.5 min-h-[44px] text-sm font-semibold text-bone/85 transition-colors hover:bg-bone/10 hover:text-bone focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
          >
            Browse movies
          </Link>
        </div>
      </div>
    </div>
  );
}

function HeroCarousel({ movieData }) {
  const swiperRef = useRef(null);
  const [active, setActive] = useState(0);
  const reduceMotion = useReducedMotion();

  return (
    <div className="relative -mx-3 sm:-mx-4 md:-mx-8 lg:-mx-12 xl:-mx-16 -mt-16 md:-mt-20 overflow-hidden film-grain">
      <Swiper
        modules={[Autoplay, A11y]}
        loop
        grabCursor
        allowTouchMove
        simulateTouch
        touchStartPreventDefault={false}
        autoplay={
          reduceMotion
            ? false
            : { delay: 7500, disableOnInteraction: false, pauseOnMouseEnter: true }
        }
        speed={900}
        onSwiper={(swiper) => {
          swiperRef.current = swiper;
        }}
        onSlideChange={(swiper) => setActive(swiper.realIndex)}
        className="w-full"
      >
        {movieData.map((movie, i) => (
          <SwiperSlide key={i}>
            <HeroSlide movie={movie} reduceMotion={reduceMotion} />
          </SwiperSlide>
        ))}
      </Swiper>

      {/* Timecode rail — desktop */}
      <div className="absolute right-6 xl:right-10 top-1/2 -translate-y-1/2 z-20 hidden lg:flex flex-col items-end gap-2.5">
        <span className="timecode text-[10px] tracking-[0.24em] uppercase text-bone/55 mb-1.5">
          Reel
        </span>
        {movieData.map((movie, i) => {
          const isActive = i === active;
          return (
            <button
              key={i}
              type="button"
              onClick={() => swiperRef.current?.slideToLoop(i)}
              aria-label={`Show ${movie.title || `slide ${i + 1}`}`}
              aria-current={isActive ? "true" : undefined}
              className="group flex items-center gap-2.5 py-1 focus-visible:outline-none"
            >
              <span
                className={`timecode text-[10px] transition-colors duration-300 ${
                  isActive
                    ? "text-projector"
                    : "text-bone/55 group-hover:text-bone/70"
                }`}
              >
                {String(i + 1).padStart(2, "0")}
              </span>
              <span
                className={`block h-px transition-all duration-300 ${
                  isActive
                    ? "w-9 bg-projector"
                    : "w-4 bg-bone/25 group-hover:bg-bone/50"
                }`}
              />
            </button>
          );
        })}
      </div>

      {/* Timecode rail — mobile / tablet */}
      <div className="absolute bottom-4 inset-x-0 z-20 flex lg:hidden items-center justify-center gap-2">
        {movieData.map((movie, i) => {
          const isActive = i === active;
          return (
            <button
              key={i}
              type="button"
              onClick={() => swiperRef.current?.slideToLoop(i)}
              aria-label={`Show ${movie.title || `slide ${i + 1}`}`}
              aria-current={isActive ? "true" : undefined}
              className={`h-[3px] rounded-full transition-all duration-300 ${
                isActive ? "w-7 bg-projector" : "w-3 bg-bone/30"
              }`}
            />
          );
        })}
        <span className="timecode text-[10px] text-bone/50 ml-1.5">
          {String(active + 1).padStart(2, "0")} /{" "}
          {String(movieData.length).padStart(2, "0")}
        </span>
      </div>

      {/* Page-bleed bottom gradient (connects hero to page bg) */}
      <div
        className="absolute bottom-0 left-0 right-0 z-10 pointer-events-none"
        style={{
          height: "clamp(60px, 15vw, 140px)",
          background: `linear-gradient(to top, ${INK} 0%, rgba(8,9,11,0.6) 50%, transparent 100%)`,
        }}
        aria-hidden="true"
      />
    </div>
  );
}

function HeroSlide({ movie, reduceMotion }) {
  const href =
    movie.media_type === "tv"
      ? `/ser/${movie.tmdb_id}`
      : `/mov/${movie.tmdb_id}`;

  const rating =
    movie.rating != null
      ? typeof movie.rating === "number"
        ? movie.rating.toFixed(1)
        : movie.rating
      : null;

  const kind = movie.media_type === "tv" ? "Series" : "Movie";
  const genres = (movie.genres || []).slice(0, 3);

  return (
    <div className="relative w-full overflow-hidden" style={{ height: HERO_H }}>
      {/* Backdrop.
          The grain used to be applied twice — once on this inner wrapper and
          again on the carousel root — so the two `::after` overlays stacked
          (0.16 over 0.16, both mix-blend-mode: overlay) and the hero read as
          noisier than the same treatment everywhere else. Only the carousel
          root keeps it now. */}
      <div className="absolute inset-0">
        <LazyLoadImage
          src={backdropUrl(movie.backdrop || movie.poster, 900)}
          alt={movie.title || ""}
          effect="blur"
          className="w-full h-full object-cover"
          style={{ objectPosition: "center 15%" }}
          wrapperClassName="w-full h-full block"
          /* The hero is the LCP element, so it must not be deferred — the
             windowing in LazyLoadImage is skipped for above-the-fold art. */
          visibleByDefault
          decoding="async"
        />
      </div>

      {/* Gradient system.
          These are tuned, not arbitrary. Measured cumulatively over the real
          backdrop these four layers used to leave only ~22% of the image
          visible on mobile and ~13% on desktop — the artwork read as a flat
          black panel with a faint glow. The three changes that fixed it:

            • the flat mobile wash dropped from .32 to .10 (it was doubling up
              with the bottom gradient that already darkens the same pixels)
            • the top gradient halved, because the fixed header now carries its
              own scrim and the two were stacking to ~97% coverage
            • the bottom gradient keeps full ink only at the very edge and
              reaches transparent sooner, so the subject's face survives

          Text legibility is held by a dedicated left/bottom scrim rather than
          by darkening the whole frame. */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: `linear-gradient(to top, ${INK} 0%, rgba(8,9,11,0.86) 20%, rgba(8,9,11,0.46) 52%, rgba(8,9,11,0.10) 76%, transparent 100%)`,
        }}
        aria-hidden="true"
      />
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "linear-gradient(to bottom, rgba(8,9,11,0.52) 0%, rgba(8,9,11,0.14) 20%, transparent 42%)",
        }}
        aria-hidden="true"
      />
      <div
        className="absolute inset-0 pointer-events-none hidden sm:block"
        style={{
          background:
            "linear-gradient(to right, rgba(8,9,11,0.84) 0%, rgba(8,9,11,0.42) 46%, rgba(8,9,11,0.06) 78%, transparent 100%)",
        }}
        aria-hidden="true"
      />
      {/* Mobile: a soft vertical scrim behind the copy instead of a flat wash
          over the entire frame, so the top half of the artwork stays legible. */}
      <div
        className="absolute inset-0 pointer-events-none sm:hidden"
        style={{
          background:
            "linear-gradient(to top, rgba(8,9,11,0.62) 0%, rgba(8,9,11,0.30) 42%, transparent 68%)",
        }}
        aria-hidden="true"
      />

      {/* Content */}
      <div
        className="absolute inset-0 flex flex-col justify-end"
        style={{
          paddingLeft: "clamp(1rem, 4vw, 4rem)",
          paddingRight: "clamp(1rem, 4vw, 4rem)",
          paddingBottom: "clamp(3.25rem, 8vw, 6rem)",
        }}
      >
        <div style={{ maxWidth: "620px", width: "100%" }}>
          <div
            /* Slide copy fade-up, now a CSS animation instead of framer.
               Same tween as before: opacity 0→1, y 22→0, 0.6s. Under reduced
               motion the class is dropped so nothing animates at all. */
            className={reduceMotion ? undefined : "animate-hero-copy"}
          >
            <span className="eyebrow mb-3 sm:mb-4">Now screening</span>

            {movie.title && (
              <h1
                style={{
                  fontFamily: "'Fraunces', Georgia, serif",
                  fontSize: "clamp(2rem, 5.6vw, 4.4rem)",
                  fontWeight: 500,
                  lineHeight: 1.02,
                  letterSpacing: "-0.025em",
                  color: "#F2EDE3",
                  textShadow: "0 2px 28px rgba(0,0,0,0.9)",
                  overflowWrap: "break-word",
                  wordBreak: "break-word",
                  maxWidth: "22ch",
                  marginBottom: "clamp(10px, 1.8vw, 16px)",
                }}
              >
                {movie.title}
              </h1>
            )}

            {/* Programme line */}
            <div className="timecode flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[10px] sm:text-[11px] uppercase tracking-[0.12em] text-bone/55 mb-3.5">
              <span className="text-projector">{kind}</span>
              {movie.release_year && (
                <>
                  <span className="text-bone/50">/</span>
                  <span>{movie.release_year}</span>
                </>
              )}
              {rating && (
                <>
                  <span className="text-bone/50">/</span>
                  <span className="inline-flex items-center gap-1">
                    <PiStarFill className="text-projector text-[9px]" />
                    {rating}
                  </span>
                </>
              )}
              {movie.rip && (
                <>
                  <span className="text-bone/50">/</span>
                  <span>{movie.rip}</span>
                </>
              )}
              {genres.length > 0 && (
                <>
                  <span className="text-bone/50">/</span>
                  <span className="text-bone/60">{genres.join(" · ")}</span>
                </>
              )}
            </div>

            {movie.description && (
              /*
               * `hidden` + `mobile-desc:block` never worked: an inline
               * `display: "-webkit-box"` beat both utilities, so the synopsis
               * showed on every phone and crowded out the CTA. The clamp now
               * lives in a class instead of the style attribute.
               */
              <p
                className="hidden mobile-desc:block line-clamp-2"
                style={{
                  fontSize: "clamp(0.78rem, 2.2vw, 0.9rem)",
                  color: "rgba(242,237,227,0.66)",
                  lineHeight: 1.6,
                  maxWidth: "42ch",
                  marginBottom: "clamp(14px, 2.4vw, 20px)",
                  textShadow: "0 1px 8px rgba(0,0,0,0.8)",
                }}
              >
                {movie.description}
              </p>
            )}

            <div style={{ marginBottom: "clamp(12px, 2vw, 18px)" }} />

            <Link
              to={href}
              aria-label={`Watch ${movie.title || kind}`}
              className="group inline-flex items-center gap-2.5 bg-projector text-ink font-bold text-[13px] sm:text-sm px-5 sm:px-7 min-h-[44px] rounded-[3px] tracking-wide shadow-[0_12px_32px_-14px_rgba(233,184,114,0.55)] hover:bg-[#F0CA98] transition-colors duration-200"
            >
              <BsPlayFill className="text-base transition-transform duration-200 group-hover:translate-x-0.5" />
              <span>Watch now</span>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
