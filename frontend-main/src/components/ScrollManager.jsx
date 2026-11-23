import { useEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

const STORAGE_KEY = "fc:scroll-map";
const MAX_RESTORE_FRAMES = 150;

function readPositions() {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

export default function ScrollManager() {
  const location = useLocation();
  const navigationType = useNavigationType();
  const positions = useRef(readPositions());

  useEffect(() => {
    if ("scrollRestoration" in window.history) {
      window.history.scrollRestoration = "manual";
    }
  }, []);

  useEffect(() => {
    let frame = 0;

    const persist = () => {
      frame = 0;
      positions.current[location.key] = window.scrollY;
      try {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(positions.current));
      } catch {
        // Storage can be unavailable in private mode; restoration is progressive enhancement.
      }
    };

    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(persist);
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
      persist();
    };
  }, [location.key]);

  useEffect(() => {
    if (navigationType === "POP") {
      const target = positions.current[location.key];
      if (!target) return undefined;

      let frame;
      let frames = 0;
      const restore = () => {
        window.scrollTo(0, target);
        frames += 1;
        if (Math.abs(window.scrollY - target) > 2 && frames < MAX_RESTORE_FRAMES) {
          frame = requestAnimationFrame(restore);
        }
      };
      frame = requestAnimationFrame(restore);
      return () => cancelAnimationFrame(frame);
    }

    if (location.hash) {
      const id = decodeURIComponent(location.hash.slice(1));
      const frame = requestAnimationFrame(() => {
        document.getElementById(id)?.scrollIntoView();
      });
      return () => cancelAnimationFrame(frame);
    }

    window.scrollTo(0, 0);
    return undefined;
  }, [location.key, location.hash, navigationType]);

  return null;
}
