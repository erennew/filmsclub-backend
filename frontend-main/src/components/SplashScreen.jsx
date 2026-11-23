import { useEffect, useRef, useState } from "react";
import useReducedMotion from "../hooks/useReducedMotion";
import { createDragons } from "../vendor/dragons";

/**
 * Launch animation.
 *
 * A full-bleed field of ASCII dragons — armoured backs, soft bellies, wing gusts
 * and the occasional breath of fire — with the brand lockup over it. The scene is
 * vendored in `src/vendor/dragons.js` (see that file for provenance and the three
 * patches); this component is only its lifecycle and its framing.
 *
 * This is a short launch surface over the app's above-the-fold loading state.
 * It never waits for below-fold shelves, and its hard ceiling prevents a slow
 * backend from turning the launch animation into a blank-screen delay.
 *
 * `ready` is the hero and Top 10 settle signal. It shortens the animation when
 * the first view is usable, while MAX_VISIBLE_MS remains the safety ceiling.
 */
const MIN_VISIBLE_MS = 720;
const MAX_VISIBLE_MS = 2200;
const FADE_MS = 220;

export default function SplashScreen({ ready }) {
  const [phase, setPhase] = useState("visible");
  const openedAt = useRef(Date.now());
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    if (phase !== "visible") return undefined;
    const timeout = setTimeout(() => setPhase("closing"), MAX_VISIBLE_MS);
    return () => clearTimeout(timeout);
  }, [phase]);

  useEffect(() => {
    if (phase !== "visible" || !ready) return undefined;
    const remaining = Math.max(0, MIN_VISIBLE_MS - (Date.now() - openedAt.current));
    const timeout = setTimeout(() => setPhase("closing"), remaining);
    return () => clearTimeout(timeout);
  }, [phase, ready]);

  useEffect(() => {
    if (phase === "hidden") return undefined;

    const previousOverflow = document.body.style.overflow;
    const previousOverscrollBehavior = document.documentElement.style.overscrollBehavior;
    document.body.style.overflow = "hidden";
    document.documentElement.style.overscrollBehavior = "none";

    return () => {
      document.body.style.overflow = previousOverflow;
      document.documentElement.style.overscrollBehavior = previousOverscrollBehavior;
    };
  }, [phase]);

  useEffect(() => {
    if (phase !== "closing") return undefined;
    const timeout = setTimeout(() => setPhase("hidden"), reduceMotion ? 0 : FADE_MS);
    return () => clearTimeout(timeout);
  }, [phase, reduceMotion]);

  if (phase === "hidden") return null;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Loading CloudXMovies"
      className={`fixed inset-0 z-[9999] h-[100dvh] w-screen max-w-full overflow-hidden bg-ink transition-opacity ease-out ${
        reduceMotion ? "duration-0" : "duration-[220ms]"
      } ${phase === "closing" ? "opacity-0" : "opacity-100"}`}
    >
      <DragonField reduceMotion={reduceMotion} active={phase === "visible"} />

      {/*
       * Scrim. The dragons are a bright, busy field — without this the brand
       * wordmark sits on moving glyphs and neither reads. Two stops rather than a
       * flat wash: dense at the centre where the lockup lives, clear at the edges
       * so the flight path stays visible and the frame does not look like a black
       * box with a hole in it.
       */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 58% 46% at 50% 50%, rgba(8,9,11,0.94) 0%, rgba(8,9,11,0.82) 42%, rgba(8,9,11,0.34) 74%, rgba(8,9,11,0.10) 100%)",
        }}
      />
      {/* Vignette: pins the corners to ink so the canvas edge never reads as a seam. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 78% 70% at 50% 50%, transparent 55%, rgba(8,9,11,0.72) 100%)",
        }}
      />

      <div className="relative flex h-full flex-col items-center justify-center px-8 text-center">
        <BrandMark reduceMotion={reduceMotion} visible={phase === "visible"} />

        <div
          className={`mt-11 h-px w-40 overflow-hidden bg-bone/10 transition-opacity ease-out ${
            reduceMotion ? "duration-0" : "duration-500 delay-300"
          } ${phase === "visible" ? "opacity-100" : "opacity-0"}`}
          aria-hidden="true"
        >
          <div
            className={`h-full w-1/2 bg-projector/70 ${reduceMotion ? "" : "animate-splash-sweep"}`}
          />
        </div>

        <p
          className={`timecode mt-3 text-[9px] uppercase tracking-[0.22em] text-bone/55 transition-opacity ease-out ${
            reduceMotion ? "duration-0" : "duration-500 delay-300"
          } ${phase === "visible" ? "opacity-100" : "opacity-0"}`}
        >
          Threading the projector
        </p>
      </div>
    </div>
  );
}

/* ── The dragon field ──────────────────────────────────────────────────────── */

/**
 * Owns the canvas + the `createDragons` handle.
 *
 * Lifecycle notes that are easy to get wrong:
 *   • The scene must be created AFTER the canvas is laid out, because it measures
 *     the stage on construction. It also watches the stage with a ResizeObserver,
 *     so the initial measurement only has to be non-zero, not final.
 *   • `destroy()` is mandatory: the scene holds a rAF loop, an IntersectionObserver,
 *     a ResizeObserver, a MutationObserver and a media-query listener. Leaking one
 *     on every cold start would stack a second flight on top of the first.
 *   • `setPaused` is wired to the fade: once the splash starts closing we stop the
 *     simulation rather than keep a full-screen scene running under an invisible
 *     element for another 420 ms.
 */
function DragonField({ reduceMotion, active }) {
  const stageRef = useRef(null);
  const canvasRef = useRef(null);
  const handleRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    if (!canvas || !stage) return undefined;

    /* Keep the decorative workload below the content startup budget. */
    const narrow = window.matchMedia("(max-width: 640px)").matches;
    const lowPower = narrow || navigator.hardwareConcurrency <= 4;

    let handle;
    try {
      handle = createDragons(canvas, {
        count: narrow ? 1 : 3,
        size: narrow ? 0.82 : 1,
        speed: 1,
        glow: lowPower ? 0.35 : 0.55,
        cell: narrow ? 6 : 7,
        colorful: false,
        cursor: false,
        grid: !lowPower,
        fire: !lowPower,
        gusts: !lowPower,
        shadows: !lowPower,
      });
      handleRef.current = handle;
    } catch (error) {
      // Never let a decorative launch animation break the boot path. The splash
      // still shows the brand lockup; only the dragons are missing.
      console.warn("Dragon splash failed to start:", error);
      return undefined;
    }

    return () => {
      handleRef.current = null;
      handle.destroy();
    };
  }, []);

  useEffect(() => {
    // Fade started: stop simulating. Plain pause (not teardown) so resuming is
    // free if the phase ever flips back.
    handleRef.current?.setPaused(reduceMotion || !active);
  }, [active, reduceMotion]);

  return (
    <div ref={stageRef} className="cx-dragon-stage absolute inset-0" aria-hidden="true">
      <canvas ref={canvasRef} className="h-full w-full" />
    </div>
  );
}

/* ── Brand lockup ──────────────────────────────────────────────────────────── */

function BrandMark({ reduceMotion, visible }) {
  return (
    <>
      <svg
        width="64"
        height="64"
        viewBox="0 0 26 26"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        className={`transition-all ease-out ${reduceMotion ? "duration-0" : "duration-700"} ${
          visible ? "scale-100 opacity-100" : "scale-[0.98] opacity-0"
        }`}
      >
        <defs>
          <linearGradient id="launchBrand" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#F0CA98" />
            <stop offset="100%" stopColor="#D9A65C" />
          </linearGradient>
        </defs>
        <path
          d="M5,9 C5,5.5 7.5,3 11,3.5 C12.5,2 15,1.5 17,3 C19.5,3 21,5 21,7.5 C23,8.5 24,10.5 23,13 C22,15.5 19.5,16 17,16 L9,16 C6.5,16 4,14 4,11.5 C4,10 4.5,9.2 5,9 Z"
          fill="url(#launchBrand)"
          opacity="0.95"
        />
        <path
          d="M8,19 L18,19 M10,22 L16,22"
          stroke="url(#launchBrand)"
          strokeWidth="2"
          strokeLinecap="round"
          opacity="0.7"
        />
      </svg>

      <h1
        className={`mt-5 font-display text-[1.75rem] tracking-[-0.02em] transition-opacity ease-out md:text-[2rem] ${
          reduceMotion ? "duration-0" : "duration-500 delay-100"
        } ${visible ? "opacity-100" : "opacity-0"}`}
      >
        <span
          className="bg-clip-text text-transparent"
          style={{ backgroundImage: "linear-gradient(110deg, #F0CA98, #D9A65C)" }}
        >
          CloudX
        </span>
        <span className="text-bone">Movies</span>
      </h1>

      <p className="mt-2 font-mono text-[9px] uppercase tracking-[0.28em] text-bone/55">
        Late-night screening room
      </p>
    </>
  );
}
