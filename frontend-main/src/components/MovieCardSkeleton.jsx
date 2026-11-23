import { useEffect, useMemo, useRef } from "react";
import { asciiSkeletonHTML, animateSkeletons, SKELETON_CSS } from "ascii-reveal/core";
import AsciiMascot from "./AsciiMascot";

/**
 * ASCII-glyph card placeholders.
 *
 * Two layers, both from ascii-reveal:
 *   • the poster panel — a dither glyph field that reserves the exact card size
 *     and paints on the first frame with no JavaScript (plain HTML + CSS);
 *   • a mascot — an animated ASCII bat centred on the poster, rendered through the
 *     same character grid, so the loading state has something alive in it instead
 *     of being pure texture.
 *
 * Why the vanilla API instead of <AsciiSkeleton>:
 *   The React component inlines the entire skeleton stylesheet (~1 KB) once per
 *   instance — React 18 has no way to hoist it, and the component exposes no
 *   `css: false` escape hatch. At 24 cards that is ~24 KB of duplicated <style>
 *   blocks in the DOM for one grid. So we inject SKELETON_CSS once and generate
 *   each placeholder from `asciiSkeletonHTML({ css: false })`.
 *
 * Animation budget:
 *   A moving shimmer costs ~21% browser CPU while twelve cards wait, and each
 *   mascot adds its own 30 fps canvas. Two budgets therefore apply, and they are
 *   the reason a long grid looks different from a short one:
 *
 *   shimmer   <= 12 placeholders. Past a dozen the grid is mostly below the fold,
 *             so it renders the still pattern (0% animation CPU, cheaper first
 *             paint than a grey box).
 *   mascot    <= 6, and never on the still-pattern grids. Six mascots was chosen
 *             after measuring: each one is a ~52×52 px offscreen buffer plus ~250
 *             fillText calls per frame, and six of them held the frame rate at the
 *             page's idle rate on a 4×-throttled phone profile, while twelve
 *             started dropping frames. A shelf is 8-10 cards, so the first six
 *             carry a mascot and the rest show texture — the eye reads the row of
 *             bats as one deliberate motif rather than as an incomplete set.
 */

let cssInjected = false;

/*
 * Placeholder count above which the shimmer is not started. Twelve cards is the
 * point where the moving shimmer was measured at ~21% browser CPU, so that is the
 * ceiling: <= 12 animates, >= 13 renders the still pattern.
 */
const SKELETON_ANIMATE_MAX_ITEMS = 12;

/** Cards that get a mascot. Measured — see the note above. */
const MASCOT_MAX_ITEMS = 6;

/** Injects the shared skeleton stylesheet exactly once per document. */
function ensureSkeletonCss() {
  if (cssInjected || typeof document === "undefined") return;
  cssInjected = true;
  if (document.querySelector("style[data-cx-ascii-skeleton]")) return;
  const style = document.createElement("style");
  style.setAttribute("data-cx-ascii-skeleton", "");
  style.textContent = SKELETON_CSS;
  document.head.appendChild(style);
}

/**
 * Builds one placeholder's markup once.
 * `seed` keeps neighbouring cards from showing an identical glyph pattern.
 */
function useSkeletonMarkup({ variant, rows, lines, widths, seed, animate }) {
  return useMemo(
    () =>
      asciiSkeletonHTML({
        variant,
        rows,
        lines,
        widths,
        seed,
        animate,
        css: false, // shared stylesheet injected above
      }),
    [variant, rows, lines, widths, seed, animate]
  );
}

/**
 * Poster panel + optional mascot.
 *
 * `mascotSize` is derived from the card width the grid will actually render at,
 * clamped so the bat stays legible on a 118px rail card and does not overwhelm a
 * 190px one. Doing it in CSS (`width: 62%`) would be simpler but the mascot canvas
 * needs concrete pixel dimensions — its glyph count is computed from them — so a
 * percentage would mean measuring the element before the first paint.
 */
function PosterBlock({ seed, animate, withMascot }) {
  const html = useSkeletonMarkup({
    variant: "dither",
    rows: 24,
    seed,
    animate,
  });
  return (
    <div className="relative aspect-[2/3] w-full overflow-hidden rounded-[4px] bg-carbon cx-ascii-poster">
      {/*
        The library always emits a 96-column glyph block, so its aspect ratio is
        fixed at render time and cannot be made to match a 2:3 poster exactly.
        We therefore set the glyph size in CSS (see .cx-ascii-poster) and let the
        grid run off the right edge, which reads as a continuing texture instead
        of stopping mid-panel. 24 rows is the smallest count that covers the
        frame's height without leaving a gap, and keeps the markup per card
        around 2 KB. Content is the library's fixed glyph ramp — never user
        input.
      */}
      <div
        className="absolute inset-0 overflow-hidden"
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {withMascot && (
        /*
         * The mascot is drawn over the panel, not instead of it: the dither keeps
         * the card's shape and tone, the bat supplies the motion. A soft ink wash
         * behind it seats it in the poster so the glyphs do not fight each other.
         *
         * Sizing is a fixed intrinsic canvas scaled by CSS, not a measured width.
         * Two reasons:
         *   • the box must be LANDSCAPE. The bat is drawn wings-out, roughly 2.2×
         *     wider than tall; handed a square box the auto-fit pass scales to the
         *     width and leaves ~60% of the height empty, which rendered a small bat
         *     floating in a large hole;
         *   • the grid has to be drawn at ONE fixed cell count, because the number
         *     of glyphs is computed from the pixel size before first paint. A
         *     measured width would mean either a layout read on mount or a second
         *     canvas resize. Drawing once at the widest card (150px) and letting
         *     CSS scale it down for narrow cards is crisper anyway: downscaling a
         *     canvas is clean, whereas drawing at 100px then upscaling is not.
         */
        <div className="absolute inset-0 flex items-center justify-center">
          <div
            aria-hidden="true"
            className="absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse 62% 40% at 50% 50%, rgba(8,9,11,0.86) 0%, rgba(8,9,11,0.5) 55%, transparent 100%)",
            }}
          />
          <AsciiMascot
            name="bat"
            width={150}
            height={68}
            fluid
            className="relative w-[86%] max-w-[150px] text-projector/85"
          />
        </div>
      )}
    </div>
  );
}

/* Hoisted so the memo below keeps a stable dependency identity. */
const TITLE_WIDTHS = ["78%", "38%"];

function TitleLines({ seed, animate }) {
  const html = useSkeletonMarkup({
    variant: "dither",
    lines: 2,
    widths: TITLE_WIDTHS,
    seed: seed + 97,
    animate,
  });
  return (
    <div
      className="mt-2 px-0.5"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

const MovieCardSkeleton = ({ count = 20, horizontal = false }) => {
  const items = Array.from({ length: horizontal ? Math.min(count, 10) : count });
  const rootRef = useRef(null);

  /*
   * Animate only small grids; long ones render the still pattern.
   *
   * History worth keeping:
   *   - `animate` was hardcoded to true because an earlier `items.length <= 12`
   *     rule made most grids static — four of six call sites ask for 20-24
   *     cards, so the app usually showed no shimmer at all and it read as
   *     broken rather than intentional.
   *   - The library is cheaper than it looks: one canvas per placeholder, only
   *     the ones its IntersectionObserver reports on-screen are painted
   *     (measured 8 of 20 at a phone viewport), all driven by one shared rAF
   *     loop, with an automatic bail-out when frames exceed ~45ms.
   *
   * This lands in the middle of those two positions: the cost that actually
   * hurts is a *large* grid — the file's own measurement is ~21% browser CPU
   * while twelve cards shimmer — so the shimmer is kept where it is affordable
   * and visible (small grids and the horizontal rails) and dropped where it is
   * mostly below the fold anyway (20/24-card grids), which render the still
   * pattern at 0% animation CPU and a cheaper first paint.
   *
   * Note `items.length` is already clamped for rails (`Math.min(count, 10)`), so
   * a horizontal shelf counts as 8-10 and animates, while a full grid of 20 or
   * 24 placeholders stays still. That split is the intended outcome.
   */
  const animate = items.length <= SKELETON_ANIMATE_MAX_ITEMS;
  /*
   * Mascots are independent of `animate`, deliberately.
   *
   * They were tied to it at first, which meant the 20- and 24-card grids (five of
   * the six call sites) showed no mascot at all — the feature existed only on the
   * homepage rails. But the two budgets are unrelated: `animate` is about paying
   * for one canvas *per card*, while a mascot is a fixed cost paid by the first
   * MASCOT_MAX_ITEMS cards only. Six mascots cost the same in a 24-card grid as in
   * an 8-card one, so there is no reason to withhold them from the long grids.
   */
  const mascots = Math.min(items.length, MASCOT_MAX_ITEMS);

  useEffect(() => {
    ensureSkeletonCss();
    // Long grids emit the still pattern, so there is no marked skeleton to
    // drive — skip starting the shared rAF loop entirely.
    if (!animate) return undefined;
    const scope = rootRef.current;
    if (!scope) return undefined;
    // Starts one shared shimmer loop for every marked skeleton in the scope.
    return animateSkeletons(scope);
  }, [animate]);

  if (horizontal) {
    return (
      <div
        ref={rootRef}
        className="ascii-scope flex gap-3 sm:gap-4 overflow-hidden pb-3"
        aria-busy="true"
      >
        <span className="sr-only">Loading titles</span>
        {items.map((_, index) => (
          <div
            key={index}
            className="flex-shrink-0"
            style={{ width: "clamp(118px, 18vw, 190px)" }}
          >
            <PosterBlock
              seed={index + 1}
              animate={animate}
              withMascot={index < mascots}
            />
            <TitleLines seed={index + 1} animate={animate} />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className="ascii-scope grid gap-x-2 sm:gap-x-3 gap-y-6 grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7"
      aria-busy="true"
    >
      <span className="sr-only">Loading titles</span>
      {items.map((_, index) => (
        <div key={index} className="relative">
          <PosterBlock
            seed={index + 1}
            animate={animate}
            withMascot={index < mascots}
          />
          <TitleLines seed={index + 1} animate={animate} />
        </div>
      ))}
    </div>
  );
};

export default MovieCardSkeleton;
