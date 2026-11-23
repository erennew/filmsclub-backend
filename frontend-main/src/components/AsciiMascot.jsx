import { useEffect, useRef } from "react";
import { mascots, resolvePreset } from "ascii-reveal/core";
import useReducedMotion from "../hooks/useReducedMotion";

/**
 * AsciiMascot — a small animated line drawing rendered *through* a character grid.
 *
 * The `ascii-reveal` package ships four mascots (spider, ghost, pumpkin, bat) plus
 * `imageMascot()` for your own SVG, but it only ever draws them inside
 * `<AsciiReveal loading mascot="…">`. That component derives its drawing area from
 * whatever it just hid, which is exactly wrong here: a card skeleton hides
 * nothing, and those area heuristics would smear one mascot across a whole shelf.
 * So this component owns its own box and reuses only the two pieces worth reusing
 * — the draw functions and the preset's glyph ramp.
 *
 * The conversion is the technique the library documents:
 *   1. Draw the mascot into an offscreen canvas at SAMPLE samples per glyph cell.
 *   2. For each cell, average the alpha of its SAMPLE×SAMPLE block.
 *   3. Map that coverage onto the ramp (" .:-=+*#%@") and paint one glyph.
 *
 * Output is text, so the mascot inherits the site's mono face and takes a single
 * flat colour — it reads as living ASCII art rather than a tiny vector logo. It is
 * `aria-hidden`: the host announces loading itself (`aria-busy` + "Loading
 * titles"), so a screen reader is never handed a wall of characters.
 *
 * ── Two things that are easy to get wrong, both fixed here ────────────────────
 *
 * 1. MIXING CSS PX AND BUFFER PX. The buffer is SAMPLE times larger than the
 *    glyph grid, so a cell is `off.width / cols` buffer pixels wide, not `cw`.
 *    The first version subtracted a CSS-px cell from a buffer-px dimension and
 *    reserved twice the intended margin — the mascot came out at 47% of its
 *    frame, a small bat floating in a large hole. Every reserve below is
 *    expressed in buffer pixels.
 *
 * 2. MEASURING A CLIPPED SILHOUETTE. The draw functions fill the box they are
 *    given but do not always stay inside it: `bat` reaches ±0.5·s across the
 *    wings while `s` is derived from the box width, so it runs past the edge;
 *    `spider` hangs its thread from the top and trails legs below. Measuring that
 *    in a buffer the size of the final area produced a *truncated* bounding box,
 *    so the fit computed a scale from an already-cut shape and the result still
 *    clipped on screen. The fit is therefore measured on a larger scratch canvas
 *    where the overflow has room to show up.
 *
 * State is sampled at several points in the motion cycle and unioned, because
 * every mascot drifts and breathes: a box fitted to one instant lets the art
 * wander out of frame a moment later.
 *
 * Cost: one small canvas at ~30 fps plus one getImageData per frame. The fit pass
 * is a handful of offscreen draws, once, at mount. Prefers-reduced-motion draws a
 * single settled frame and stops. Treat this as a loading indicator — unmount it
 * when the data lands.
 */

/**
 * Glyph cell height in CSS px.
 *
 * The library's mascot loop uses 11px cells, which suits a full-screen hero. In a
 * ~160px card the mascot needs a denser grid to stay readable: at 11px a bat gets
 * 9×5 cells and reads as two blobs. 5px gives ~44×13 for the same box — enough
 * for wings, ears and the body to separate — while the glyphs stay large enough
 * to survive being painted at 1× on a non-retina screen.
 */
const CELL = 5;
/** Mono cells are taller than wide; 0.62 is the library's ratio. */
const CELL_W = CELL * 0.62;
/** Offscreen samples per glyph cell, each way.
 *
 * This is the single most important number in the file. The library's own mascot
 * loop uses 2×2 samples per cell, which is fine on a full-screen hero where the
 * cells are 11px and the art is large. In a ~160px card the same art is scaled to
 * a fifth of its size, and at 3 samples per cell (a ~144×30 buffer) a 1.5px stroke
 * falls between samples and smears across whole cells: measured, the bat rendered
 * as horizontal stripes because no cell ever saw the full stroke weight.
 *
 * At 6 samples per cell the same buffer is 288×84 — 24k pixels, still trivial for
 * one getImageData per frame — and the stroke density survives. Verified by
 * rendering the glyph grid and comparing it against the raw vector silhouette:
 * 6 is the first setting where the wings, ears, body and tail all separate.
 */
const SAMPLE = 6;
/** The art only changes shape every few frames; 30 fps is indistinguishable. */
const FRAME_MS = 33;
/** Below this ink coverage a cell gets no glyph (a space is a wasted fillText). */
const MIN_INK = 0.05;
/**
 * Coverage → ramp gamma.
 *
 * Coverage is measured as the MEAN alpha of a cell's samples, not its strongest
 * one. Measured comparison on the bat: max-sampling collapses it into a solid blob
 * (any cell the stroke merely grazes jumps to "@", so the notch between the wings
 * and the taper of the tail disappear), while mean-sampling reproduces both.
 *
 * With mean coverage the midway values cluster low even when the art is solid —
 * a cell at the edge of a wing is partly covered — so a straight mapping puts most
 * of the drawing in the first three glyphs. 0.55 was chosen by sweeping 0.4-1.0 and
 * reading the rendered glyph grid back against the raw vector silhouette: below 0.4
 * everything clips to "@" and the wing notch closes, above 0.7 the thin tail drops
 * out entirely.
 */
const INK_GAMMA = 0.55;
/** Fraction of the usable area the art may occupy. */
const FILL = 0.92;
/**
 * How much larger the fit-measurement scratch canvas is than the drawing area.
 * 1.7 leaves room for the mascots' own overflow so the union box is measured from
 * a complete silhouette rather than one truncated by the buffer edge.
 */
const SCRATCH_SCALE = 1.7;
/** Poses sampled for the union box. Spread across the ~2-3 s motion cycles. */
const FIT_POSES = [0, 0.6, 1.2, 1.8, 2.4, 3.0];

const RAMP = resolvePreset("decode").ramp;

/**
 * Alpha-weighted bounding box of a rendered mascot, in the buffer's own pixels.
 * `into` accumulates across calls so several poses can share one box.
 * Returns the box, or null when nothing was drawn (e.g. an SVG still decoding).
 */
function inkBox(data, width, height, into) {
  const box = into || { minX: width, maxX: -1, minY: height, maxY: -1 };
  for (let y = 0; y < height; y++) {
    const row = y * width;
    for (let x = 0; x < width; x++) {
      if (data[(row + x) * 4 + 3] > 8) {
        if (x < box.minX) box.minX = x;
        if (x > box.maxX) box.maxX = x;
        if (y < box.minY) box.minY = y;
        if (y > box.maxY) box.maxY = y;
      }
    }
  }
  return box.maxX < 0 ? null : box;
}

export default function AsciiMascot({
  name = "bat",
  draw,
  width = 112,
  height = 126,
  color,
  className = "",
  /** Track the parent's width instead of using the intrinsic pixel size. */
  fluid = false,
}) {
  const canvasRef = useRef(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const drawFn = typeof draw === "function" ? draw : mascots[name];
    if (!drawFn) return undefined;

    const dpr = Math.min(2, window.devicePixelRatio || 1);

    // Fit a whole number of cells into the requested box; cells stretch by a
    // fraction of a px so the grid covers it exactly. Rounding the count down and
    // leaving the remainder as dead space makes the canvas slightly smaller than
    // its layout slot, and the art then sits visibly off-centre in it.
    const cols = Math.max(4, Math.round(width / CELL_W));
    const rows = Math.max(4, Math.round(height / CELL));
    const cw = width / cols;
    const ch = height / rows;

    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    // Intrinsic size is fixed at the drawing resolution so the grid is crisp; a
    // caller that needs it to track a card's width passes `fluid` and lets CSS
    // scale it down. Downscaling a canvas resamples cleanly, upscaling does not.
    canvas.style.width = fluid ? "100%" : `${width}px`;
    canvas.style.height = fluid ? "auto" : `${height}px`;
    canvas.style.aspectRatio = `${width} / ${height}`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return undefined;

    /*
     * Drawing buffer. The buffer must carry the SAME aspect ratio as the CSS box,
     * because the draw functions fill whatever w×h they are handed — a buffer that
     * sampled every cell as a square SAMPLE×SAMPLE grid would squash the art
     * vertically, and the glyph loop would then lay that squashed drawing back
     * over tall mono cells. So cells are SAMPLE wide and proportionally taller,
     * giving buffer-cell counts SX × SY. (The library's own mascot loop applies the
     * same cw/ch correction.)
     *
     * Sampling a whole block per cell, rather than one point, matters on the thin
     * 2px strokes these mascots are drawn with: single-point reads alias badly.
     */
    const SX = SAMPLE;
    const SY = Math.max(1, Math.round(SAMPLE * (ch / cw)));
    const off = document.createElement("canvas");
    off.width = cols * SX;
    off.height = rows * SY;
    const octx = off.getContext("2d", { willReadFrequently: true });
    if (!octx) return undefined;

    // One glyph cell, in BUFFER pixels. Every reserve below is expressed in these,
    // never in CSS px (see note 1 in the file header).
    const cellBufW = SX;
    const cellBufH = SY;

    // Scratch canvas for the fit measurement — larger, so the draw functions'
    // own overflow is measurable instead of being clipped at the edge.
    const scratch = document.createElement("canvas");
    scratch.width = Math.round(off.width * SCRATCH_SCALE);
    scratch.height = Math.round(off.height * SCRATCH_SCALE);
    const sctx = scratch.getContext("2d", { willReadFrequently: true });
    if (!sctx) return undefined;

    // Measure the union of the ink boxes across the motion cycle.
    const fit = (() => {
      let box = null;
      for (const t of FIT_POSES) {
        sctx.setTransform(1, 0, 0, 1, 0, 0);
        sctx.clearRect(0, 0, scratch.width, scratch.height);
        drawFn(sctx, scratch.width, scratch.height, t, "#fff");
        box = inkBox(
          sctx.getImageData(0, 0, scratch.width, scratch.height).data,
          scratch.width,
          scratch.height,
          box
        );
      }
      if (!box) return null;

      const bw = Math.max(1, box.maxX - box.minX);
      const bh = Math.max(1, box.maxY - box.minY);

      /*
       * Fit the union into the usable area, reserving ONE CELL on every side.
       * A glyph is painted centred in its cell and a dense ramp character ("@")
       * is nearly a full advance wide, so the outermost columns spill past their
       * cell — and the canvas clips that spill. Reserving a cell means the fit is
       * computed against where a glyph's *centre* may sit, so no painted pixel
       * can reach the canvas edge. Both reserves are in buffer px.
       */
      const usableW = Math.max(1, off.width - cellBufW * 2);
      const usableH = Math.max(1, off.height - cellBufH * 2);
      const s = Math.min(usableW / bw, usableH / bh) * FILL;

      return {
        s,
        // Centre the union box in the buffer.
        x: (off.width - bw * s) / 2 - box.minX * s,
        y: (off.height - bh * s) / 2 - box.minY * s,
        // Virtual box handed to drawFn during live rendering. Must be the scratch
        // size: `s`/`x`/`y` map scratch coordinates into the buffer, so drawing
        // with the buffer's own dimensions would apply a second, differently
        // scaled layout and throw the art back outside the frame.
        vw: scratch.width,
        vh: scratch.height,
      };
    })();

    /**
     * Per-cell coverage for one pose, as the MEAN alpha of the cell's samples.
     *
     * Mean rather than max: measured on the bat, max-sampling turns the drawing
     * into a solid blob because any cell the stroke merely grazes jumps to full
     * density, losing the notch between the wings and the taper of the tail. Mean
     * keeps the shape. SAMPLE is 6 for this reason — see its comment.
     */
    const coverage = (t) => {
      if (!fit) return null;
      octx.setTransform(1, 0, 0, 1, 0, 0);
      octx.clearRect(0, 0, off.width, off.height);
      octx.setTransform(fit.s, 0, 0, fit.s, fit.x, fit.y);
      drawFn(octx, fit.vw, fit.vh, t, "#ffffff");
      octx.setTransform(1, 0, 0, 1, 0, 0);
      const data = octx.getImageData(0, 0, off.width, off.height).data;

      const out = new Float32Array(cols * rows);
      const per = SX * SY;
      for (let gy = 0; gy < rows; gy++) {
        const y0 = gy * SY;
        for (let gx = 0; gx < cols; gx++) {
          const x0 = gx * SX;
          let sum = 0;
          for (let sy = 0; sy < SY; sy++) {
            for (let sx = 0; sx < SX; sx++) {
              sum += data[((y0 + sy) * off.width + x0 + sx) * 4 + 3];
            }
          }
          out[gy * cols + gx] = sum / (255 * per);
        }
      }
      return out;
    };

    /*
     * ONE reference level for the whole animation, not one per frame.
     *
     * Normalising each frame against its own densest cells was the next attempt,
     * and it made the mascot flicker in density: as the wings sweep, the set of
     * "solid" cells changes, so the scale moved under otherwise identical strokes
     * and the whole drawing visibly breathed between dim and bright. A fixed
     * reference taken at mount keeps every stroke's glyph stable frame to frame.
     *
     * The value is the 90th percentile of the non-empty cells across the sampled
     * poses — a stable "typical solid stroke". The 90th rather than the maximum
     * because a stray double-stroked joint (a wing root, an eye) is far denser than
     * any normal stroke and, used as the top of the scale, drove every ordinary
     * stroke down to "-" and "=".
     */
    const refInk = (() => {
      const all = [];
      for (const t of FIT_POSES) {
        const c = coverage(t);
        if (!c) continue;
        for (let i = 0; i < c.length; i++) if (c[i] >= MIN_INK) all.push(c[i]);
      }
      if (!all.length) return 0;
      all.sort((a, b) => a - b);
      return Math.max(MIN_INK, all[Math.floor(all.length * 0.9)]);
    })();

    /*
     * Resolve ink and font ONCE. Both were per-frame `getComputedStyle` calls in
     * the first cut, which forces a style recalc 30 times a second for two values
     * that cannot change while this component is mounted.
     *
     * Colour is the canvas's own computed `color`, so a caller paints the mascot
     * with an ordinary Tailwind text utility and it lands on exactly the same
     * token as the copy beside it.
     */
    const cs = getComputedStyle(canvas);
    const ink = color || cs.color || "#e9b872";
    const font = `600 ${Math.round(ch * 0.92)}px ${cs.fontFamily}`;

    /** Paints one frame of the mascot as glyphs. */
    const paint = (t) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.font = font;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = ink;

      const cover = coverage(t);
      if (!cover || !refInk) {
        // Nothing measurable — an SVG that has not decoded yet, or art that drew
        // no ink at all. Draw the vector directly so the card shows something
        // rather than sitting blank, and let a later frame's fit take over.
        drawFn(ctx, width, height, t, ink);
        return;
      }

      /*
       * Map coverage onto the ramp against the FIXED mount-time reference, so a
       * given stroke always gets the same glyph. See `refInk` for why the reference
       * is not recomputed per frame, and `coverage` for why it is the densest
       * sample per cell rather than the mean.
       */
      const topIdx = RAMP.length - 1;
      for (let i = 0; i < cover.length; i++) {
        const a = cover[i];
        if (a < MIN_INK) continue;
        const level = Math.min(1, Math.pow(a / refInk, INK_GAMMA));
        const idx = Math.max(1, Math.min(topIdx, Math.round(level * topIdx)));
        const gx = i % cols;
        const gy = (i / cols) | 0;
        ctx.fillText(RAMP[idx], (gx + 0.5) * cw, (gy + 0.5) * ch);
      }
    };

    if (reduceMotion) {
      paint(0.7); // a settled pose, not the very first frame
      return undefined;
    }

    let raf = 0;
    let last = -Infinity;
    const t0 = performance.now();
    const loop = (now) => {
      raf = requestAnimationFrame(loop);
      if (now - last < FRAME_MS) return;
      last = now;
      paint((now - t0) / 1000);
    };
    raf = requestAnimationFrame(loop);

    return () => cancelAnimationFrame(raf);
  }, [name, draw, width, height, color, reduceMotion, fluid]);

  return <canvas ref={canvasRef} aria-hidden="true" className={`block ${className}`} />;
}
