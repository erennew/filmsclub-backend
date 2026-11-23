import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import useHapticFeedback from "../hooks/useHapticFeedback";
import useReducedMotion from "../hooks/useReducedMotion";

/**
 * Floating glass navigation dock for phones and small tablets.
 *
 * Replaces the old inline dock that lived at the bottom of Nav.jsx. Extracted
 * so the desktop header and the mobile dock stop sharing one 900-line component,
 * but it deliberately reuses the same `NAV_ITEMS`, the same `navStatus` active
 * logic and the same routes — it is a presentation change only.
 *
 * The visual model is three stacked planes:
 *
 *   1. the dock surface — translucent carbon glass with a blurred backdrop,
 *      a lit top edge and a soft ambient shadow beneath it;
 *   2. a travelling cradle — a radial shade that darkens the surface directly
 *      under the active item, so the raised orb reads as resting *in* the dock
 *      rather than pasted on top of it;
 *   3. the orb — a raised amber glass disc carrying the active icon, protruding
 *      above the dock's top edge.
 *
 * Both the cradle and the orb are parked by the same index custom property
 * (`--mob-dock-i`) and moved with a CSS `left` transition, so they travel in
 * lockstep between cells. This replaced the shared-layout `layoutId` technique,
 * which pulled the whole animation library into the first-load bundle for a
 * single element.
 */

const ORB = 38; // px — diameter of the raised active disc

export default function MobileDock({ items, activeName, onNavigate }) {
  const reduceMotion = useReducedMotion();
  const { navigation: hapticNavigation } = useHapticFeedback();

  /*
   * Hide the dock while the on-screen keyboard is up.
   *
   * The dock is `position: fixed` against the visual viewport on iOS, so an open
   * keyboard pushes it up to sit on top of the keyboard — floating over the
   * field the user is typing into, and covering the search suggestions. There is
   * no CSS-only signal for "keyboard open", so this compares the visual viewport
   * against the layout viewport. The 140px threshold clears browser toolbars
   * (~60-90px) without tripping on them, since those change height on scroll.
   */
  const [keyboardOpen, setKeyboardOpen] = useState(false);

  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return undefined;
    const check = () => {
      const gap = window.innerHeight - vv.height - (vv.offsetTop || 0);
      setKeyboardOpen(gap > 140);
    };
    check();
    vv.addEventListener("resize", check);
    vv.addEventListener("scroll", check);
    return () => {
      vv.removeEventListener("resize", check);
      vv.removeEventListener("scroll", check);
    };
  }, []);

  const activeIndex = items.findIndex((item) => item.name === activeName);

  // Shared timing for the cradle and the orb so they never drift apart.
  const travel = reduceMotion
    ? "none"
    : "left 420ms cubic-bezier(0.22, 1, 0.36, 1)";

  return (
    <nav
      aria-label="Primary"
      className="mob-dock navlg:hidden"
      data-keyboard-open={keyboardOpen ? "true" : "false"}
      aria-hidden={keyboardOpen ? "true" : undefined}
    >
      <div className="mob-dock__inner">
        {/* Plane 1 — the glass surface. */}
        <div className="mob-dock__surface">
          {/* Plane 2 — the cradle, parked under the active cell. */}
          {activeIndex >= 0 && (
            <span
              aria-hidden="true"
              className="mob-dock__cradle"
              style={{
                "--mob-dock-i": activeIndex,
                transition: travel,
              }}
            />
          )}

          {/* Plane 3 — the raised orb. Rendered once and parked by index, so
              it slides between tabs instead of being unmounted/remounted.
              It sits *before* the grid so the raised icon (painted inside the
              grid's stacking context) still shows on top of it. */}
          {activeIndex >= 0 && (
            <span
              aria-hidden="true"
              className="mob-dock__orb mob-dock__orb--travelling"
              style={{
                "--mob-dock-i": activeIndex,
                width: ORB,
                height: ORB,
                transition: travel,
              }}
            />
          )}

          <div className="mob-dock__grid">
            {items.map((item) => {
              const isActive = item.name === activeName;
              return (
                <Link
                  key={item.name}
                  to={item.path}
                  aria-current={isActive ? "page" : undefined}
                  className={`mob-dock__tab ${isActive ? "is-active" : ""}`}
                  onClick={() => {
                    onNavigate?.(item.name);
                    hapticNavigation();
                  }}
                >
                  <span
                    className={`mob-dock__tabinner${
                      reduceMotion ? "" : " mob-dock__tabinner--pressable"
                    }`}
                  >
                    {/* Icon stays in the flow so hiding it doesn't shift the
                        label; the visible copy rides inside the orb. */}
                    <item.icon
                      className="mob-dock__icon"
                      style={isActive ? { opacity: 0 } : undefined}
                      aria-hidden="true"
                    />
                    {isActive && (
                      <item.icon className="mob-dock__icon mob-dock__icon--raised" aria-hidden="true" />
                    )}
                    <span className="mob-dock__label">{item.name}</span>
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      </div>
    </nav>
  );
}
