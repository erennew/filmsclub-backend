import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { TG_URL, SITENAME } from "../config";
import { BsTelegram } from "react-icons/bs";
import { FiDownload } from "react-icons/fi";

const FOOTER_LINKS = [
  { label: "Home", to: "/" },
  { label: "Movies", to: "/Movies" },
  { label: "Series", to: "/Series" },
  { label: "Anime", to: "/anime" },
  { label: "K-Drama", to: "/k-drama" },
  { label: "Top Rated Movies", to: "/top-rated-movies" },
  { label: "Top Rated Series", to: "/top-rated-series" },
];

const CATEGORIES = [
  "Movies",
  "TV Shows",
  "Anime",
  "K-Drama",
  "HD Streaming",
  "Latest Releases",
];

export default function Footer() {
  const [canInstall, setCanInstall] = useState(false);

  useEffect(() => {
    setCanInstall(Boolean(window.__pwaInstallable));
    const onInstallability = (event) => setCanInstall(Boolean(event.detail));
    window.addEventListener("pwa:installability", onInstallability);
    return () => window.removeEventListener("pwa:installability", onInstallability);
  }, []);

  return (
    <footer className="relative mt-16 border-t border-bone/8 bg-gradient-to-b from-[#0B0C10] to-ink">
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-2/3 h-px bg-gradient-to-r from-transparent via-projector/30 to-transparent" />

      <div className="max-w-[1800px] mx-auto px-4 sm:px-6 md:px-8 lg:px-12 xl:px-16 pt-12 pb-[calc(var(--safe-bottom)+6.5rem)] md:pt-16 md:pb-16">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-10">
          {/* Brand Column */}
          <div className="sm:col-span-2 lg:col-span-1">
            <Link to="/" className="inline-flex items-center gap-2 mb-4">
              <svg width="22" height="22" viewBox="0 0 26 26" fill="none" xmlns="http://www.w3.org/2000/svg">
                <defs>
                  <linearGradient id="footerBrand" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#F0CA98" />
                    <stop offset="100%" stopColor="#D9A65C" />
                  </linearGradient>
                </defs>
                <path d="M5,9 C5,5.5 7.5,3 11,3.5 C12.5,2 15,1.5 17,3 C19.5,3 21,5 21,7.5 C23,8.5 24,10.5 23,13 C22,15.5 19.5,16 17,16 L9,16 C6.5,16 4,14 4,11.5 C4,10 4.5,9.2 5,9 Z" fill="url(#footerBrand)" opacity="0.95"/>
                <path d="M8,19 L18,19 M10,22 L16,22" stroke="url(#footerBrand)" strokeWidth="2" strokeLinecap="round" opacity="0.7"/>
              </svg>
              <span className="text-lg font-extrabold tracking-tight">
                <span style={{ background: 'linear-gradient(135deg, #F0CA98, #D9A65C)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' }}>CloudX</span><span className="text-bone">Movies</span>
              </span>
            </Link>
            <p className="text-sm text-bone/50 leading-relaxed max-w-xs">
              A late-night screening room for movies, TV series, anime, and
              K-Dramas. No account, no noise — just the film.
            </p>

            <div className="mt-5 flex flex-wrap items-center gap-2.5">
              {TG_URL && (
                <a
                  href={TG_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2.5 px-4 py-2.5 rounded-[3px] bg-[#229ED9]/10 hover:bg-[#229ED9]/18 border border-[#229ED9]/25 hover:border-[#229ED9]/40 text-[#4FB3E5] text-sm font-semibold transition-all duration-200"
                >
                  <BsTelegram className="text-lg" />
                  Join our Telegram
                </a>
              )}
              {canInstall && (
                <button
                  type="button"
                  onClick={() => window.dispatchEvent(new Event("pwa:install-request"))}
                  className="inline-flex items-center gap-2.5 px-4 py-2.5 rounded-[3px] bg-projector/10 hover:bg-projector/18 border border-projector/30 hover:border-projector/50 text-projector text-sm font-semibold transition-all duration-200 active:scale-95"
                >
                  <FiDownload className="text-base" />
                  Install the app
                </button>
              )}
            </div>
          </div>

          {/* Categories */}
          <div>
            <h3 className="timecode text-[10px] uppercase tracking-[0.18em] text-bone/60 mb-5">
              Content
            </h3>
            <div className="flex flex-wrap gap-2">
              {CATEGORIES.map((cat) => (
                <span
                  key={cat}
                  className="px-3 py-1.5 text-[11px] font-semibold rounded-[2px] bg-bone/5 border border-bone/10 text-bone/55 hover:text-bone/90 hover:bg-bone/8 transition-colors cursor-default"
                >
                  {cat}
                </span>
              ))}
            </div>
          </div>

          {/* Quick Links */}
          <div>
            <h3 className="timecode text-[10px] uppercase tracking-[0.18em] text-bone/60 mb-5">
              Quick Links
            </h3>
            <ul className="space-y-3">
              {FOOTER_LINKS.map((link) => (
                <li key={link.to}>
                  <Link
                    to={link.to}
                    className="text-[13px] text-bone/50 hover:text-bone transition-colors duration-200 flex items-center gap-2 group"
                  >
                    <span className="w-1 h-1 rounded-full bg-projector/40 group-hover:bg-projector transition-colors duration-200 shrink-0" />
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Divider */}
        <div className="mt-12 pt-6 border-t border-bone/8 flex flex-col sm:flex-row items-center justify-between gap-3">
          <p className="timecode text-[10px] tracking-[0.08em] text-bone/55 text-center sm:text-left">
            © {new Date().getFullYear()} {SITENAME}. All rights reserved.
          </p>
          <p className="timecode text-[10px] tracking-[0.08em] text-bone/50">
            Curated nightly · Built for film lovers
          </p>
        </div>
      </div>
    </footer>
  );
}
