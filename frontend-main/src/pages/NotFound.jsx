import React from "react";
import { Link } from "react-router-dom";
import { BiHomeAlt2 } from "react-icons/bi";
import { HiArrowLeft } from "react-icons/hi2";

/**
 * The 404 page used to render a Lottie animation, which pulled lottie-react
 * (~317 KB raw / 82 KB gzip) plus a JSON animation into its own route chunk —
 * by far the heaviest asset in the app, loaded for a page nobody wants to see.
 * This SVG is a few hundred bytes of markup and reads better at small sizes.
 */
function ReelMissing() {
  return (
    <svg
      viewBox="0 0 240 160"
      role="img"
      aria-label="A film reel with a torn frame"
      className="w-full max-w-[240px]"
    >
      <defs>
        <linearGradient id="nf-reel" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#E9B872" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#8A6A3E" stopOpacity="0.55" />
        </linearGradient>
      </defs>

      {/* Reel body */}
      <circle
        cx="120"
        cy="80"
        r="52"
        fill="none"
        stroke="url(#nf-reel)"
        strokeWidth="3"
      />
      <circle
        cx="120"
        cy="80"
        r="16"
        fill="none"
        stroke="#E9B872"
        strokeOpacity="0.55"
        strokeWidth="2"
      />

      {/* Spokes */}
      {[0, 60, 120, 180, 240, 300].map((deg) => (
        <line
          key={deg}
          x1="120"
          y1="80"
          x2={120 + 46 * Math.cos((deg * Math.PI) / 180)}
          y2={80 + 46 * Math.sin((deg * Math.PI) / 180)}
          stroke="#E9B872"
          strokeOpacity="0.32"
          strokeWidth="2"
        />
      ))}

      {/* Perforations around the rim */}
      {Array.from({ length: 12 }).map((_, i) => {
        const a = (i * 30 * Math.PI) / 180;
        return (
          <circle
            key={i}
            cx={120 + 62 * Math.cos(a)}
            cy={80 + 62 * Math.sin(a)}
            r="3.4"
            fill="#F2EDE3"
            fillOpacity="0.22"
          />
        );
      })}

      {/* The cut: a torn diagonal slash with the film end peeling away */}
      <path
        d="M78 128 L120 80 L162 32"
        stroke="#FF5A3C"
        strokeOpacity="0.85"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeDasharray="7 6"
      />
      <path
        d="M162 32 l16 -7 l-4 16 z"
        fill="#FF5A3C"
        fillOpacity="0.85"
      />
    </svg>
  );
}

const NotFoundPage = () => {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-bone px-4 text-center">
      <ReelMissing />

      <span className="eyebrow mb-3 mt-6">404 · Reel missing</span>
      <h1 className="section-title text-2xl sm:text-3xl mb-2">
        This frame was cut
      </h1>
      <p className="text-bone-subtle text-sm sm:text-base mb-8 max-w-sm leading-relaxed">
        The page you&apos;re looking for doesn&apos;t exist or has been moved.
      </p>

      <div className="flex items-center gap-3 flex-wrap justify-center">
        <button
          type="button"
          onClick={() => window.history.back()}
          className="flex items-center gap-2 px-5 py-2.5 min-h-[44px] rounded-[3px] bg-bone/[0.06] hover:bg-bone/[0.12] border border-bone/15 text-bone text-sm font-semibold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
        >
          <HiArrowLeft className="text-base" aria-hidden="true" />
          Go Back
        </button>
        <Link
          to="/"
          className="flex items-center gap-2 px-6 py-2.5 min-h-[44px] rounded-[3px] bg-projector hover:bg-projector-bright text-ink text-sm font-bold transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
          style={{ textDecoration: "none" }}
        >
          <BiHomeAlt2 className="text-base" aria-hidden="true" />
          Home
        </Link>
      </div>
    </div>
  );
};

export default NotFoundPage;
