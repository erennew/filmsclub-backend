import { useCallback, useEffect, useState } from "react";
import { PiHeartStraight, PiHeartStraightFill } from "react-icons/pi";
import { isInWatchlist, toggleWatchlist, subscribeWatchlist } from "../utils/watchlist";
import { useHapticFeedback } from "../hooks/useHapticFeedback";

/**
 * Heart toggle for "My List".
 * variant="overlay" — round icon button for posters
 * variant="detail"  — labelled pill for detail pages
 */
export default function WatchlistButton({ item, variant = "overlay", size = "md", className = "" }) {
  const tmdbId = item?.tmdb_id;
  const mediaType = item?.media_type;
  const [saved, setSaved] = useState(() => isInWatchlist(item));
  const { selection: hapticSelection } = useHapticFeedback();

  useEffect(() => {
    const lookup = { tmdb_id: tmdbId, media_type: mediaType };
    setSaved(isInWatchlist(lookup));
    return subscribeWatchlist(() => setSaved(isInWatchlist(lookup)));
  }, [tmdbId, mediaType]);

  const handleClick = useCallback(
    (event) => {
      event.preventDefault();
      event.stopPropagation();
      hapticSelection();
      setSaved(toggleWatchlist(item));
    },
    [item, hapticSelection]
  );

  const label = saved ? "Remove from My List" : "Add to My List";
  const Icon = saved ? PiHeartStraightFill : PiHeartStraight;

  if (variant === "detail") {
    return (
      <button
        type="button"
        onClick={handleClick}
        aria-pressed={saved}
        aria-label={label}
        title={label}
        className={`
          flex items-center gap-2 px-4 py-2 rounded-[3px] border text-xs font-semibold
          transition-colors duration-200 active:scale-[0.98]
          ${saved
            ? "bg-projector/15 border-projector/45 text-projector hover:bg-projector/20"
            : "bg-bone/5 border-bone/10 text-bone/60 hover:bg-bone/10 hover:text-bone"
          }
          ${className}
        `}
      >
        <Icon className="text-sm" />
        <span>{saved ? "In My List" : "My List"}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-pressed={saved}
      aria-label={label}
      title={label}
      className={`
        flex items-center justify-center rounded-full border
        backdrop-blur-md transition-all duration-200
        active:scale-90
        ${size === "sm" ? "w-7 h-7" : "w-8 h-8"}
        ${saved
          ? "bg-projector text-ink border-projector shadow-[0_6px_18px_-6px_rgba(233,184,114,0.85)]"
          : "bg-ink/75 text-bone/75 border-bone/15 hover:text-bone hover:border-bone/35"
        }
        ${className}
      `}
    >
      <Icon className={size === "sm" ? "text-[13px]" : "text-[15px]"} />
    </button>
  );
}
