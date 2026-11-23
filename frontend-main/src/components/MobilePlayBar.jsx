import React, { useEffect, useState } from "react";
import { BsPlayFill } from "react-icons/bs";
import useHapticFeedback from "../hooks/useHapticFeedback";

export default function MobilePlayBar({ title, meta, onPlay, label = "Play" }) {
  const [visible, setVisible] = useState(false);
  const { selection: hapticSelection } = useHapticFeedback();

  useEffect(() => {
    const update = () => {
      const y = window.scrollY;
      const nearBottom =
        y + window.innerHeight > document.documentElement.scrollHeight - 240;
      setVisible(y > 340 && !nearBottom);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  return (
    <div
      aria-hidden={!visible}
      className={`fixed inset-x-3 z-40 transition-all duration-300 md:hidden ${
        visible
          ? "translate-y-0 opacity-100"
          : "pointer-events-none translate-y-4 opacity-0"
      }`}
      style={{ bottom: "calc(var(--safe-bottom) + var(--mob-dock-footprint, 73px) + 0.5rem)" }}
    >
      <div className="mx-auto flex max-w-md items-center gap-3 rounded-[16px] border border-bone/10 bg-carbon/95 p-2 pl-4 shadow-[0_18px_44px_-16px_rgba(0,0,0,0.9)] backdrop-blur-2xl">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-semibold leading-tight text-bone">
            {title}
          </p>
          {meta && (
            <p className="timecode mt-0.5 truncate text-[9px] uppercase tracking-[0.16em] text-bone/50">
              {meta}
            </p>
          )}
        </div>
        <button
          type="button"
          tabIndex={visible ? 0 : -1}
          onClick={() => {
            hapticSelection();
            onPlay?.();
          }}
          className="flex shrink-0 items-center gap-1.5 rounded-[10px] bg-projector px-4 py-2.5 text-xs font-bold text-ink shadow-[0_4px_14px_rgba(233,184,114,0.25)] transition-all duration-200 hover:bg-[#F0CA98] active:scale-95"
        >
          <BsPlayFill className="text-base" />
          {label}
        </button>
      </div>
    </div>
  );
}
