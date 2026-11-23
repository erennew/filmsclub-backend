import { useEffect, useState } from "react";

/**
 * Minimal `prefers-reduced-motion` hook.
 *
 * This replaces framer-motion's `useReducedMotion` on the first-load path. Those
 * components only ever needed the *value* of the media query — never any of
 * framer's animation machinery — so importing the whole library for one boolean
 * dragged the 112KB `vendor-motion` chunk into the eager entry graph. Listening
 * to the query directly with `matchMedia` keeps the same behaviour with no
 * dependency.
 *
 * Returns `true` when the visitor has asked for reduced motion. It also tracks
 * the change event, so toggling the OS setting updates live (framer does the
 * same).
 */
const QUERY = "(prefers-reduced-motion: reduce)";

function readPreference() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia(QUERY).matches;
}

export default function useReducedMotion() {
  const [reduced, setReduced] = useState(readPreference);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return undefined;
    }

    const mql = window.matchMedia(QUERY);
    const onChange = (event) => setReduced(event.matches);

    // Re-read on mount: the first render may have happened before hydration
    // or with a different system setting.
    setReduced(mql.matches);

    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    }

    // Safari < 14 only exposes the deprecated API.
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, []);

  return reduced;
}
