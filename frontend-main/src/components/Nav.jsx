import React, { useEffect, useState, useRef, useCallback } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LazyLoadImage } from "react-lazy-load-image-component";
import "react-lazy-load-image-component/src/effects/black-and-white.css";
import { BASE_URL as BASE, SITENAME } from "../config";

import { FiSearch, FiX, FiTrendingUp, FiClock } from "react-icons/fi";
import { BiStar } from "react-icons/bi";
import { PiTelevisionSimple, PiHeartStraight, PiHouseSimple, PiFilmStrip, PiSparkle, PiStar } from "react-icons/pi";
import posterPlaceholder from "../assets/images/poster-placeholder.png";
import { posterUrl } from "../utils/imageUrl";
import useHapticFeedback from "../hooks/useHapticFeedback";
import { getWatchlist, subscribeWatchlist } from "../utils/watchlist";
import MobileDock from "./MobileDock";

/*
 * One icon family for the whole dock.
 *
 * These five previously came from four different libraries — BiHomeAlt2
 * (Bootstrap, filled), TbMovie (Tabler), PiTelevisionSimple (Phosphor),
 * MdLocalFireDepartment (Material, filled) and PiHeartStraight (Phosphor).
 * Mixing filled and outline glyphs at the same px size means the optical
 * weights can never match: the filled ones read heavy and the outline ones read
 * thin, which is what made the bar look amateur regardless of spacing.
 *
 * All five are now Phosphor, matched to a single 1.5 stroke weight. The glyphs
 * also carry more meaning than before: a film strip for Movies, a screen for
 * Series, a sparkle for Anime, and a simple house for Home (PiHouseSimple is
 * lighter than the old filled BiHomeAlt2, so it sits with the rest).
 */
const NAV_ITEMS = [
  { icon: PiHouseSimple, name: "Home", path: "/" },
  { icon: PiFilmStrip, name: "Movies", path: "/Movies" },
  { icon: PiTelevisionSimple, name: "Series", path: "/Series" },
  { icon: PiSparkle, name: "Anime", path: "/anime" },
  { icon: PiStar, name: "K-Drama", path: "/k-drama" },
];

const TRENDING_SUGGESTIONS = ["Oppenheimer", "Dune", "The Bear", "Spider-Man", "Barbie"];

/**
 * Ratings arrive from the API as either a number or a string depending on the
 * document ("8.4" vs 8.4). Calling `rating.toFixed(1)` on the string threw
 * `TypeError`, and because the throw happened inside the search-results render
 * it took down the whole app — no error boundary existed at the time.
 */
function formatRating(rating) {
  if (rating === null || rating === undefined || rating === "") return null;
  const value = Number(rating);
  if (!Number.isFinite(value)) return null;
  return value.toFixed(1);
}

// ─── Stable components defined OUTSIDE Nav so React doesn't remount them ──────

const DesktopResultItem = React.memo(({ result, onClickResult }) => (
  <button
    type="button"
    onMouseDown={(e) => e.preventDefault()} // prevent input blur / outside-click handler
    onClick={() => onClickResult(result)}
    className="flex items-center w-full gap-3 transition-colors duration-150 hover:bg-bone/5 active:bg-bone/10 py-2.5 px-4 border-b border-bone/5 last:border-0 group cursor-pointer text-left"
  >
    <div className="flex-shrink-0 w-11 h-16 rounded-[3px] overflow-hidden bg-bone/10">
      <LazyLoadImage
        alt={result.title}
        src={posterUrl(result.poster || posterPlaceholder, 44)}
        effect="black-and-white"
        className="object-cover w-full h-full"
      />
    </div>
    <div className="flex-1 min-w-0">
      <p className="line-clamp-1 text-sm font-semibold text-bone group-hover:text-projector transition-colors">
        {result.title}
      </p>
      <div className="flex items-center gap-2 mt-1 flex-wrap">
        <span className="timecode text-[10px] uppercase tracking-[0.12em] px-1.5 py-0.5 rounded-[2px] bg-bone/8 text-bone/55">
          {result.media_type === "tv" ? "Series" : "Movie"}
        </span>
        {formatRating(result.rating) && (
          <span className="flex items-center gap-0.5 text-[10px] text-projector font-medium timecode">
            <BiStar />
            {formatRating(result.rating)}
          </span>
        )}
        {result.release_year && (
          <span className="text-bone-subtle text-[10px] timecode">{result.release_year}</span>
        )}
      </div>
    </div>
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      fill="none"
      className="text-bone/50 group-hover:text-projector/70 transition-colors shrink-0"
    >
      <path
        d="M2 5h6M5 2l3 3-3 3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  </button>
));

const MobileResultItem = React.memo(({ result, onClickResult }) => (
  <button
    type="button"
    onClick={() => onClickResult(result)}
    className="flex items-center w-full gap-3 p-3 bg-bone/5 rounded-[4px] border border-bone/8 hover:bg-bone/8 hover:border-bone/15 active:scale-[0.99] transition-all duration-150 cursor-pointer text-left"
  >
    <div className="flex-shrink-0 w-14 h-20 rounded-[3px] overflow-hidden bg-bone/10">
      <LazyLoadImage
        alt={result.title}
        src={result.poster || posterPlaceholder}
        effect="black-and-white"
        className="object-cover w-full h-full"
      />
    </div>
    <div className="flex-1 min-w-0">
      <p className="line-clamp-2 text-sm font-semibold text-bone leading-tight">
        {result.title}
      </p>
      <div className="flex items-center gap-2 mt-1.5 flex-wrap">
        <span className="timecode text-[10px] uppercase tracking-[0.12em] px-1.5 py-0.5 rounded-[2px] bg-bone/8 text-bone/55">
          {result.media_type === "tv" ? "Series" : "Movie"}
        </span>
        {formatRating(result.rating) && (
          <span className="flex items-center gap-0.5 text-[10px] text-projector font-medium timecode">
            <BiStar />
            {formatRating(result.rating)}
          </span>
        )}
        {result.release_year && (
          <span className="text-bone-subtle text-[10px] timecode">{result.release_year}</span>
        )}
      </div>
      {result.overview && (
        <p className="text-[11px] text-bone/55 line-clamp-1 mt-1">{result.overview}</p>
      )}
    </div>
  </button>
));

// ─────────────────────────────────────────────────────────────────────────────

export default function Nav() {
  const [query, setQuery] = useState("");
  const [debouncedVal, setDebouncedVal] = useState("");
  const [searchResult, setSearchResult] = useState([]);
  const [scrolled, setScrolled] = useState(false);
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const [navStatus, setNavStatus] = useState("Home");
  const [recentSearches, setRecentSearches] = useState([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [watchlistCount, setWatchlistCount] = useState(0);

  const location = useLocation();
  const navigate = useNavigate();
  // `navigation` haptics moved into MobileDock along with the dock markup.
  const { selection: hapticSelection } = useHapticFeedback();
  const searchRef = useRef(null);
  const mobileInputRef = useRef(null);
  const mobileOverlayRef = useRef(null);
  // Track mobile-search state in a ref so the pointerdown handler (which closes
  // on stale closures) can read the current value without re-registering.
  const isMobileSearchOpenRef = useRef(false);

  useEffect(() => {
    isMobileSearchOpenRef.current = isMobileSearchOpen;
  }, [isMobileSearchOpen]);

  // Load recent searches
  useEffect(() => {
    try {
      const saved = localStorage.getItem("fc_recent_searches");
      if (saved) setRecentSearches(JSON.parse(saved));
    } catch (e) {}
  }, []);

  // Keep the My List badge in sync with hearts anywhere on the page
  useEffect(() => {
    const sync = () => setWatchlistCount(getWatchlist().length);
    sync();
    return subscribeWatchlist(sync);
  }, []);

  const saveRecentSearch = useCallback((term) => {
    if (!term.trim()) return;
    setRecentSearches((prev) => {
      const updated = [term, ...prev.filter((s) => s !== term)].slice(0, 6);
      try { localStorage.setItem("fc_recent_searches", JSON.stringify(updated)); } catch (e) {}
      return updated;
    });
  }, []);

  // Scroll effect
  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  // Active nav tracking.
  //
  // The old version tested `path.startsWith("/ser")`, which also matches
  // `/search/...` — so the Series tab lit up on search results. It also missed
  // /top-rated-*, /most-viewed, /genre/*, /my-list and /Cast/* entirely, which
  // left the dock with no active item on those pages. Use an explicit,
  // longest-match-first prefix map instead.
  useEffect(() => {
    const path = location.pathname;
    const ROUTE_TAB = [
      ["/mov/", "Movies"],
      ["/Movies", "Movies"],
      ["/top-rated-movies", "Movies"],
      ["/most-viewed", "Movies"],
      ["/genre/movie", "Movies"],
      ["/similarMov/", "Movies"],
      ["/ser/", "Series"],
      ["/Series", "Series"],
      ["/top-rated-series", "Series"],
      ["/genre/tv", "Series"],
      ["/similarSeries/", "Series"],
      ["/anime", "Anime"],
      ["/k-drama", "K-Drama"],
    ];
    if (path === "/") {
      setNavStatus("Home");
      return;
    }
    const hit = ROUTE_TAB.find(
      ([prefix]) => path === prefix || path.startsWith(prefix)
    );
    setNavStatus(hit ? hit[1] : "");
  }, [location.pathname]);

  // Fetch search results
  useEffect(() => {
    // Clearing the query mid-flight used to leave `isLoading` true forever,
    // which showed an endless "Searching…" spinner.
    if (!debouncedVal) {
      setSearchResult([]);
      setIsLoading(false);
      setSearchError(false);
      return undefined;
    }
    const ctrl = new AbortController();
    setIsLoading(true);
    setSearchError(false);
    fetch(`${BASE}/api/search/?query=${encodeURIComponent(debouncedVal)}&page=1`, {
      signal: ctrl.signal,
    })
      .then((r) => {
        if (!r.ok) throw new Error(`Search failed (${r.status})`);
        return r.json();
      })
      .then((data) => {
        setSearchResult(data.results || []);
        setIsLoading(false);
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        // Surface the failure instead of showing "No results", which used to
        // make a backend outage look like an empty catalogue.
        setSearchResult([]);
        setSearchError(true);
        setIsLoading(false);
      });
    return () => ctrl.abort();
  }, [debouncedVal]);

  // Debounce
  useEffect(() => {
    const t = setTimeout(() => setDebouncedVal(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  // Outside-click closes the DESKTOP dropdown only.
  // IMPORTANT: skip completely when the mobile search overlay is open so we
  // don't accidentally clear results when the user taps a result card.
  useEffect(() => {
    const handler = (e) => {
      // If mobile search is open, never interfere — the mobile overlay handles itself.
      if (isMobileSearchOpenRef.current) return;
      if (searchRef.current && !searchRef.current.contains(e.target)) {
        setDropdownOpen(false);
        setDebouncedVal("");
        setSearchResult([]);
        setQuery("");
      }
    };
    document.addEventListener("pointerdown", handler);
    return () => document.removeEventListener("pointerdown", handler);
  }, []);

  // Auto-focus mobile input
  useEffect(() => {
    if (isMobileSearchOpen && mobileInputRef.current) {
      setTimeout(() => mobileInputRef.current?.focus(), 120);
    }
  }, [isMobileSearchOpen]);

  /*
   * Mobile search overlay: scroll lock + dialog keyboard behaviour.
   *
   * Two bugs lived here.
   * 1. The lock wrote `document.body.style.overflow = "hidden"` and only undid it
   *    in the effect cleanup. The overlay is also hidden by a `hidden` utility
   *    above md, so when a phone-width overlay was open and the viewport was
   *    widened past md, the overlay disappeared from the layout *without*
   *    `isMobileSearchOpen` changing — the lock stayed applied and the page
   *    could never scroll again. The lock is now released whenever the overlay
   *    is not actually on screen, and re-checked on resize.
   * 2. It was not a dialog: no role, no aria-modal, no Escape, no focus trap,
   *    so keyboard and screen-reader users could tab straight out of the
   *    "modal" into the page behind it.
   */
  useEffect(() => {
    if (!isMobileSearchOpen) return undefined;

    const overlay = mobileOverlayRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const releaseIfOffscreen = () => {
      // Once the overlay is display:none (>= md, or closed), the lock must go.
      const visible = overlay && overlay.offsetParent !== null;
      if (!visible) document.body.style.overflow = previousOverflow || "";
    };
    window.addEventListener("resize", releaseIfOffscreen);
    releaseIfOffscreen();

    return () => {
      window.removeEventListener("resize", releaseIfOffscreen);
      document.body.style.overflow = previousOverflow || "";
    };
  }, [isMobileSearchOpen]);

  // Escape closes the overlay; Tab is trapped inside it while open.
  useEffect(() => {
    if (!isMobileSearchOpen) return undefined;

    const onKeyDown = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setIsMobileSearchOpen(false);
        return;
      }
      if (e.key !== "Tab") return;

      const root = mobileOverlayRef.current;
      if (!root) return;
      const focusables = root.querySelectorAll(
        'a[href], button:not([disabled]), input, textarea, select, [tabindex]:not([tabindex="-1"])'
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [isMobileSearchOpen]);

  const clearSearch = useCallback(() => {
    setQuery("");
    setDebouncedVal("");
    setSearchResult([]);
    setDropdownOpen(false);
  }, []);

  // ── Desktop result click ──────────────────────────────────────────────────
  const handleDesktopResultClick = useCallback((result) => {
    const path = result.media_type === "movie"
      ? `/mov/${result.tmdb_id}`
      : `/ser/${result.tmdb_id}`;
    saveRecentSearch(result.title);
    // Navigate FIRST, then clean up — avoids race with dropdown closing
    navigate(path);
    clearSearch();
  }, [navigate, clearSearch, saveRecentSearch]);

  // ── Mobile result click ───────────────────────────────────────────────────
  const handleMobileResultClick = useCallback((result) => {
    const path = result.media_type === "movie"
      ? `/mov/${result.tmdb_id}`
      : `/ser/${result.tmdb_id}`;
    saveRecentSearch(result.title);
    // Navigate FIRST so it isn't swallowed by unmount
    navigate(path);
    // Close overlay after navigation is queued
    setIsMobileSearchOpen(false);
    clearSearch();
  }, [navigate, clearSearch, saveRecentSearch]);

  const handleSeeAll = useCallback(() => {
    const term = debouncedVal;
    navigate(`/search/${encodeURIComponent(term)}`);
    clearSearch();
  }, [debouncedVal, navigate, clearSearch]);

  const handleMobileSeeAll = useCallback(() => {
    const term = debouncedVal;
    navigate(`/search/${encodeURIComponent(term)}`);
    setIsMobileSearchOpen(false);
    clearSearch();
  }, [debouncedVal, navigate, clearSearch]);

  return (
    <>
      {/* ── TOP NAV BAR ── */}
      <div
        className={`
          fixed top-0 left-0 right-0 z-50
          flex items-center justify-between gap-3
          pt-[calc(var(--safe-top)+0.75rem)] pb-3 px-4
          md:pt-[calc(var(--safe-top)+1rem)] md:pb-4 md:px-8 lg:px-12
          text-bone transition-all duration-300 ease-in-out
          ${scrolled
            ? "bg-ink/95 backdrop-blur-xl shadow-[0_4px_30px_rgba(0,0,0,0.6)] border-b border-bone/8"
            : "cx-nav-scrim"
          }
        `}
      >
        {/* Logo */}
        <Link
          to="/"
          className="flex items-center gap-2 shrink-0 hover:opacity-90 transition-opacity duration-200"
          onClick={() => setNavStatus("Home")}
        >
          {/* CloudXMovies brand mark */}
          <svg width="26" height="26" viewBox="0 0 26 26" fill="none" xmlns="http://www.w3.org/2000/svg" className="shrink-0">
            <defs>
              <linearGradient id="navBrand" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stopColor="#F0CA98" />
                <stop offset="100%" stopColor="#D9A65C" />
              </linearGradient>
            </defs>
            <path d="M5,9 C5,5.5 7.5,3 11,3.5 C12.5,2 15,1.5 17,3 C19.5,3 21,5 21,7.5 C23,8.5 24,10.5 23,13 C22,15.5 19.5,16 17,16 L9,16 C6.5,16 4,14 4,11.5 C4,10 4.5,9.2 5,9 Z" fill="url(#navBrand)" opacity="0.95"/>
            <path d="M8,19 L18,19 M10,22 L16,22" stroke="url(#navBrand)" strokeWidth="2" strokeLinecap="round" opacity="0.7"/>
          </svg>
          <span className="font-extrabold text-lg md:text-xl tracking-tight">
            <span style={{ background: 'linear-gradient(135deg, #F0CA98, #D9A65C)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>CloudX</span><span className="text-bone">Movies</span>
          </span>
        </Link>

        {/* Desktop nav links.
            Gated on `navlg` (990px), not `md` (700px): the full desktop header
            needs ~990px for the logo + five icon+label links + My List + the
            search field, so switching at 700px squeezed the nav into the input
            anywhere from 700–990px. `navlg` is a dedicated breakpoint so none of
            the app's other `md:` utilities reflow. */}
        <nav className="hidden navlg:flex items-center gap-1">
          {NAV_ITEMS.map((item) => {
            const isActive = navStatus === item.name;
            return (
              <Link
                key={item.name}
                to={item.path}
                onClick={() => setNavStatus(item.name)}
                className={`
                  relative flex items-center gap-2 px-3 py-2
                  text-[13px] font-semibold tracking-wide transition-colors duration-200
                  ${isActive
                    ? "text-bone"
                    : "text-bone/55 hover:text-bone"
                  }
                `}
              >
                <item.icon className={`text-base ${isActive ? "text-projector" : "text-bone/60"}`} />
                <span>{item.name}</span>
                {isActive && (
                  /* Active underline. Previously a framer `layoutId` pill that
                     slid between links; the sliding needed the library in the
                     eager bundle, so it is now a plain span that fades in under
                     the active item. */
                  <span
                    aria-hidden="true"
                    className="absolute left-2 right-2 -bottom-[7px] h-px bg-projector nav-active-pill"
                  />
                )}
              </Link>
            );
          })}
        </nav>

        {/* Right side */}
        <div className="flex items-center gap-2">
          {/* My List */}
          <Link
            to="/my-list"
            aria-label={watchlistCount > 0 ? `My List, ${watchlistCount} saved` : "My List"}
            title="My List"
            className="relative flex items-center justify-center w-10 h-10 rounded-full border border-bone/10 bg-bone/[0.06] backdrop-blur-xl shadow-[inset_0_1px_0_rgba(242,237,227,0.06)] transition-all duration-200 hover:border-projector/30 hover:bg-bone/10 active:scale-95"
          >
            <PiHeartStraight className={`text-[17px] ${watchlistCount > 0 ? "text-projector" : "text-bone/80"}`} />
            {watchlistCount > 0 && (
              <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-projector text-ink timecode text-[9px] font-bold leading-none flex items-center justify-center">
                {watchlistCount > 99 ? "99+" : watchlistCount}
              </span>
            )}
          </Link>

          {/* Mobile search icon */}
          <button
            onClick={() => {
              hapticSelection();
              setIsMobileSearchOpen(true);
            }}
            className="navlg:hidden flex items-center justify-center w-10 h-10 rounded-full border border-bone/10 bg-bone/[0.06] backdrop-blur-xl shadow-[inset_0_1px_0_rgba(242,237,227,0.06)] transition-all duration-200 hover:border-projector/30 hover:bg-bone/10 active:scale-95"
            aria-label="Open Search"
          >
            <FiSearch className="text-[17px] text-bone/80" />
          </button>

          {/* Desktop search */}
          <form
            onSubmit={(e) => { e.preventDefault(); if (debouncedVal) handleSeeAll(); }}
            className="hidden navlg:block relative"
            ref={searchRef}
          >
            <div className="relative group flex items-center">
              <FiSearch className="absolute left-3.5 text-bone/50 group-focus-within:text-projector transition-colors duration-200 z-10 text-sm" />
              <input
                type="text"
                name="search"
                value={query}
                onChange={(e) => { setQuery(e.target.value); setDropdownOpen(true); }}
                onFocus={() => setDropdownOpen(true)}
                placeholder="Search movies, series, anime…"
                className="
                  w-52 lg:w-72 xl:w-80
                  py-2.5 pl-10 pr-10
                  outline-none text-sm
                  border border-bone/12
                  rounded-[3px]
                  placeholder-bone/40
                  focus:border-projector/60
                  focus:w-64 lg:focus:w-80 xl:focus:w-96
                  transition-all duration-300 font-medium
                "
                autoComplete="off"
              />
              {query && (
                <button
                  type="button"
                  onClick={clearSearch}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-bone/60 hover:text-bone transition-colors z-10"
                >
                  <FiX className="text-sm" />
                </button>
              )}
            </div>

            {/* Desktop dropdown.
                The framer enter/exit tween (opacity 0→1, y -6→0, scale .98→1)
                is now a CSS entrance animation, `nav-dropdown-in`. Exit is not
                animated: the node unmounts immediately, which is imperceptible
                at this 150ms scale and avoids keeping a stale dropdown mounted
                over the page. */}
            {dropdownOpen && (query || recentSearches.length > 0) && (
              <div className="absolute top-[calc(100%+10px)] right-0 z-50 w-[400px] nav-dropdown-in">
                <div className="bg-carbon/95 backdrop-blur-2xl border border-bone/10 rounded-[4px] overflow-hidden shadow-[0_24px_60px_rgba(0,0,0,0.8)]">
                    {isLoading ? (
                      <div className="p-6 flex flex-col items-center gap-3">
                        <div className="w-8 h-8 border-2 border-projector border-t-transparent rounded-full animate-spin" />
                        <p className="text-bone/50 text-xs">Searching…</p>
                      </div>
                    ) : searchError ? (
                      <div className="p-8 text-center" role="alert">
                        <div className="w-12 h-12 rounded-full bg-signal/10 flex items-center justify-center mx-auto mb-3">
                          <FiSearch className="text-signal/70 text-xl" />
                        </div>
                        <p className="text-bone/70 text-sm font-medium">
                          Search is unavailable
                        </p>
                        <p className="text-bone/50 text-xs mt-1">
                          We couldn&apos;t reach the library. Try again in a moment.
                        </p>
                      </div>
                    ) : searchResult.length > 0 ? (
                      <>
                        <div className="px-4 pt-3 pb-1">
                          <p className="timecode text-[10px] uppercase tracking-[0.18em] text-bone/60">
                            Results for "{debouncedVal}"
                          </p>
                        </div>
                        <div className="max-h-[60dvh] overflow-y-auto">
                          {searchResult.slice(0, 8).map((result) => (
                            <DesktopResultItem
                              key={result.tmdb_id}
                              result={result}
                              onClickResult={handleDesktopResultClick}
                            />
                          ))}
                        </div>
                        {searchResult.length > 5 && (
                          <div className="p-3 border-t border-bone/8">
                            <button
                              type="button"
                              onMouseDown={(e) => e.preventDefault()}
                              onClick={handleSeeAll}
                              className="flex items-center justify-center gap-2 w-full py-2.5 bg-projector hover:bg-projector-bright text-ink font-bold rounded-[3px] transition-colors duration-200 text-sm"
                            >
                              <FiSearch className="text-sm" />
                              See all {searchResult.length}+ results
                            </button>
                          </div>
                        )}
                      </>
                    ) : debouncedVal ? (
                      <div className="p-8 text-center">
                        <div className="w-12 h-12 rounded-full bg-bone/5 flex items-center justify-center mx-auto mb-3">
                          <FiSearch className="text-bone/55 text-xl" />
                        </div>
                        <p className="text-bone/55 text-sm">
                          No results for{" "}
                          <span className="text-bone font-medium">"{debouncedVal}"</span>
                        </p>
                      </div>
                    ) : recentSearches.length > 0 ? (
                      <div className="py-2">
                        <div className="px-4 py-2">
                          <p className="timecode text-[10px] uppercase tracking-[0.18em] text-bone/55 flex items-center gap-1.5">
                            <FiClock className="text-xs" /> Recent
                          </p>
                        </div>
                        {recentSearches.slice(0, 5).map((s, i) => (
                          <button
                            key={i}
                            type="button"
                            onMouseDown={(e) => e.preventDefault()}
                            onClick={() => { setQuery(s); setDebouncedVal(s); setDropdownOpen(true); }}
                            className="flex items-center gap-3 w-full px-4 py-2.5 hover:bg-bone/5 transition-colors text-sm text-bone/70 hover:text-bone"
                          >
                            <FiClock className="text-bone/50 shrink-0 text-xs" />
                            {s}
                          </button>
                        ))}
                      </div>
                    ) : null}
                </div>
              </div>
            )}
          </form>
        </div>
      </div>

      {/* ── MOBILE FULL-SCREEN SEARCH ── */}
      {isMobileSearchOpen && (
        <div
          ref={mobileOverlayRef}
          role="dialog"
          aria-modal="true"
          aria-label="Search"
          className="fixed inset-0 z-[60] navlg:hidden flex flex-col nav-overlay-in"
          style={{ background: "#08090B" }}
        >
            {/* Header */}
            <div
              className="flex items-center gap-3 px-4 border-b border-bone/8"
              style={{
                paddingTop: "calc(env(safe-area-inset-top, 0px) + 14px)",
                paddingBottom: "14px",
              }}
            >
              <button
                onClick={() => { setIsMobileSearchOpen(false); clearSearch(); }}
                aria-label="Close search"
                className="flex items-center justify-center w-10 h-10 rounded-full bg-bone/[0.06] hover:bg-bone/10 border border-bone/10 backdrop-blur-xl transition-all active:scale-95 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70"
              >
                <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
                  <path
                    d="M11 4L6 9l5 5"
                    stroke="#F2EDE3"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>

              <div className="relative flex-1">
                <FiSearch className="absolute left-3.5 top-1/2 -translate-y-1/2 text-bone/60 text-base pointer-events-none" />
                <input
                  ref={mobileInputRef}
                  type="text"
                  name="mobile-search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search movies, series, anime…"
                  className="w-full py-3 pl-10 pr-10 outline-none text-sm rounded-[3px] border border-bone/10 focus:border-projector/60 transition-all"
                  style={{
                    color: "#F2EDE3",
                    caretColor: "#E9B872",
                    WebkitTextFillColor: "#F2EDE3",
                    backgroundColor: "rgba(255,255,255,0.05)",
                  }}
                  autoComplete="off"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    className="absolute right-3 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full bg-bone/12 flex items-center justify-center hover:bg-bone/20 transition-colors"
                  >
                    <FiX className="text-bone/70 text-xs" />
                  </button>
                )}
              </div>
            </div>

            {/* Results / Empty states */}
            <div className="flex-1 overflow-y-auto pb-32">
              {isLoading ? (
                <div className="flex flex-col items-center justify-center py-20 gap-4">
                  <div className="w-10 h-10 border-2 border-projector border-t-transparent rounded-full animate-spin" />
                  <p className="text-bone/50 text-sm">Searching…</p>
                </div>
              ) : searchError ? (
                <div
                  role="alert"
                  className="flex flex-col items-center justify-center py-20 gap-4 px-8 text-center"
                >
                  <div className="w-20 h-20 bg-signal/10 rounded-full flex items-center justify-center">
                    <FiSearch className="text-3xl text-signal/70" />
                  </div>
                  <div>
                    <p className="text-bone/75 text-base font-medium">
                      Search is unavailable
                    </p>
                    <p className="text-bone/50 text-sm mt-1">
                      We couldn&apos;t reach the library. Try again in a moment.
                    </p>
                  </div>
                </div>
              ) : searchResult.length > 0 ? (
                <div className="p-4 space-y-2">
                  <p className="timecode text-[10px] uppercase tracking-[0.18em] text-bone/60 px-1 mb-3">
                    Results for "{debouncedVal}"
                  </p>
                  {searchResult.slice(0, 12).map((result) => (
                    <MobileResultItem
                      key={result.tmdb_id}
                      result={result}
                      onClickResult={handleMobileResultClick}
                    />
                  ))}
                  {searchResult.length > 5 && (
                    <button
                      type="button"
                      onClick={handleMobileSeeAll}
                      className="flex items-center justify-center gap-2 w-full mt-3 py-3.5 bg-projector text-ink font-bold rounded-[3px] text-sm active:scale-[0.99] transition-all"
                    >
                      <FiSearch />
                      See all {searchResult.length}+ results
                    </button>
                  )}
                </div>
              ) : debouncedVal ? (
                <div className="flex flex-col items-center justify-center py-20 gap-4 px-8 text-center">
                  <div className="w-20 h-20 bg-bone/5 rounded-full flex items-center justify-center">
                    <FiSearch className="text-3xl text-bone/55" />
                  </div>
                  <div>
                    <p className="text-bone/70 text-base font-medium">No results found</p>
                    <p className="text-bone/50 text-sm mt-1">Try a different search term</p>
                  </div>
                </div>
              ) : (
                <div className="p-4 space-y-6">
                  {recentSearches.length > 0 && (
                    <div>
                      <div className="flex items-center justify-between mb-3 px-1">
                        <p className="timecode text-[10px] uppercase tracking-[0.18em] text-bone/55 flex items-center gap-1.5">
                          <FiClock className="text-xs" /> Recent
                        </p>
                        <button
                          onClick={() => {
                            setRecentSearches([]);
                            localStorage.removeItem("fc_recent_searches");
                          }}
                          className="timecode text-[10px] uppercase tracking-[0.14em] text-projector/70 hover:text-projector font-medium transition-colors"
                        >
                          Clear all
                        </button>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {recentSearches.map((s, i) => (
                          <button
                            key={i}
                            onClick={() => setQuery(s)}
                            className="flex items-center gap-2 px-3.5 py-2 bg-bone/5 border border-bone/10 rounded-[3px] text-sm text-bone/70 hover:bg-bone/10 hover:text-bone transition-all active:scale-95"
                          >
                            <FiClock className="text-[10px] text-bone/55" />
                            {s}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div>
                    <p className="timecode text-[10px] uppercase tracking-[0.18em] text-bone/55 px-1 mb-3 flex items-center gap-1.5">
                      <FiTrendingUp className="text-xs" /> Trending
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {TRENDING_SUGGESTIONS.map((s, i) => (
                        <button
                          key={i}
                          onClick={() => setQuery(s)}
                          className="px-4 py-2 bg-bone/5 border border-bone/10 rounded-[3px] text-sm text-bone/70 hover:text-bone hover:border-projector/40 transition-all active:scale-95 font-medium"
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
      )}

      {/* ── MOBILE BOTTOM NAVIGATION — floating glass dock ──
          Extracted to MobileDock.jsx. It shares NAV_ITEMS, navStatus and the
          routes with the desktop header, so this is a presentation change only.
          Hidden at the same `navlg` breakpoint as the desktop header, so the
          two chrome modes swap at exactly one viewport width. */}
      <MobileDock
        items={NAV_ITEMS}
        activeName={navStatus}
        onNavigate={setNavStatus}
      />
    </>
  );
}
