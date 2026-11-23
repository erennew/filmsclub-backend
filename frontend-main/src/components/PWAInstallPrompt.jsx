import React, { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  FiDownload,
  FiPlusSquare,
  FiRefreshCw,
  FiShare,
  FiSmartphone,
  FiWifiOff,
  FiX,
} from "react-icons/fi";
import { MdOutlineOfflineBolt } from "react-icons/md";
import { SITENAME } from "../config";
import { trackEvent } from "../utils/analytics";

const DISMISS_KEY = "pwa-install-dismissed-at";
const DISMISS_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;

const isStandalone = () =>
  window.matchMedia?.("(display-mode: standalone)")?.matches ||
  window.navigator.standalone === true;

const isIos = () => {
  const ua = window.navigator.userAgent;
  return (
    /iphone|ipad|ipod/i.test(ua) ||
    (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1)
  );
};

const installDismissedRecently = () => {
  try {
    const at = Number(localStorage.getItem(DISMISS_KEY) || 0);
    return Date.now() - at < DISMISS_WINDOW_MS;
  } catch {
    return false;
  }
};

const notifyInstallable = (canInstall) => {
  window.__pwaInstallable = canInstall;
  window.dispatchEvent(
    new CustomEvent("pwa:installability", { detail: canInstall })
  );
};

export default function PWAInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [installOpen, setInstallOpen] = useState(false);
  const [iosOpen, setIosOpen] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [updateReady, setUpdateReady] = useState(() => Boolean(window.__pwaUpdateReady));
  const [online, setOnline] = useState(() => window.navigator.onLine !== false);
  const deferredPromptRef = useRef(null);

  useEffect(() => {
    const goOnline = () => {
      setOnline(true);
      trackEvent("pwa_online");
    };
    const goOffline = () => {
      setOnline(false);
      trackEvent("pwa_offline");
    };
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  useEffect(() => {
    const onUpdate = () => setUpdateReady(true);
    window.addEventListener("pwa:update-available", onUpdate);
    return () => window.removeEventListener("pwa:update-available", onUpdate);
  }, []);

  useEffect(() => {
    if (isStandalone()) return undefined;
    let timer;
    const dismissed = installDismissedRecently();

    /*
     * The sheet is a floating card anchored above the dock, so on a phone it
     * lands in the middle of the first screen. Measured at 320x568 it covered
     * 43% of the viewport and overlapped the hero's primary "Watch now" CTA —
     * an install prompt nobody asked for, sitting on the one action the page
     * exists for. Auto-opening is therefore gated on the hero being out of the
     * way: the prompt appears only once the visitor has scrolled past the first
     * screen. The footer's "Install the app" button (pwa:install-request) still
     * opens it on demand at any time, so nothing is lost by waiting.
     */
    const HERO_CLEAR_FRACTION = 0.6;
    const FIRST_CHECK_MS = 4500;
    const POLL_MS = 2500;
    const MAX_POLLS = 12; // ~35s of grace, after which the footer owns it
    const heroIsClear = () =>
      (window.scrollY || 0) >= window.innerHeight * HERO_CLEAR_FRACTION;

    const scheduleAutoOpen = (open) => {
      let polls = 0;
      const attempt = () => {
        if (heroIsClear()) {
          open();
          return;
        }
        // Still on the hero: wait for the visitor to scroll past it, but stop
        // re-checking eventually rather than holding a timer open forever.
        if (polls >= MAX_POLLS) return;
        polls += 1;
        timer = window.setTimeout(attempt, POLL_MS);
      };
      timer = window.setTimeout(attempt, FIRST_CHECK_MS);
    };

    const onBeforeInstall = (event) => {
      event.preventDefault();
      setDeferredPrompt(event);
      deferredPromptRef.current = event;
      notifyInstallable(true);
      if (!dismissed) {
        scheduleAutoOpen(() => {
          setInstallOpen(true);
          trackEvent("pwa_install_prompt", { method: "browser" });
        });
      }
    };
    const onInstalled = () => {
      setInstallOpen(false);
      setIosOpen(false);
      setDeferredPrompt(null);
      deferredPromptRef.current = null;
      notifyInstallable(false);
      trackEvent("pwa_installed");
    };
    const onInstallRequest = () => {
      if (deferredPromptRef.current) {
        setIosOpen(false);
        setInstallOpen(true);
        trackEvent("pwa_install_prompt", { method: "footer" });
      } else if (isIos()) {
        setInstallOpen(false);
        setIosOpen(true);
        trackEvent("pwa_install_prompt", { method: "ios" });
      }
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    window.addEventListener("appinstalled", onInstalled);
    window.addEventListener("pwa:install-request", onInstallRequest);

    if (isIos()) {
      notifyInstallable(true);
      if (!dismissed) {
        // Same scroll gate as the Chromium path above.
        scheduleAutoOpen(() => {
          setIosOpen(true);
          trackEvent("pwa_install_prompt", { method: "ios" });
        });
      }
    }

    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onInstalled);
      window.removeEventListener("pwa:install-request", onInstallRequest);
    };
  }, []);

  const dismissInstall = useCallback((reason = "manual") => {
    setInstallOpen(false);
    setIosOpen(false);
    try {
      localStorage.setItem(DISMISS_KEY, String(Date.now()));
    } catch {
      // Storage can be unavailable in private mode; dismissing still works.
    }
    trackEvent("pwa_install_dismissed", { reason });
  }, []);

  const handleInstall = useCallback(async () => {
    if (!deferredPrompt) {
      setIosOpen(true);
      return;
    }
    setInstalling(true);
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    setInstalling(false);
    setDeferredPrompt(null);
    deferredPromptRef.current = null;
    if (outcome === "accepted") {
      setInstallOpen(false);
      trackEvent("pwa_install_accepted", { method: "browser" });
    } else {
      dismissInstall("native_decline");
    }
  }, [deferredPrompt, dismissInstall]);

  const applyUpdate = useCallback(() => {
    window.__pwaUpdateReady = false;
    setUpdateReady(false);
    trackEvent("pwa_update_applied");
    window.dispatchEvent(new Event("pwa:apply-update"));
  }, []);

  const installSheetOpen = installOpen || iosOpen;

  return (
    <>
      {/* Update + offline status, pinned below the top bar */}
      <div className="pointer-events-none fixed inset-x-3 top-[calc(var(--safe-top)+4.25rem)] z-[55] mx-auto flex max-w-md flex-col items-center gap-2 md:top-24">
        <AnimatePresence>
          {!online && (
            <motion.div
              role="status"
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="pointer-events-auto flex items-center gap-2 rounded-full border border-projector/20 bg-carbon/90 px-3.5 py-2 shadow-[0_10px_30px_rgba(0,0,0,0.55)] backdrop-blur-xl"
            >
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-projector/60" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-projector" />
              </span>
              <FiWifiOff className="text-[11px] text-projector" />
              <span className="timecode text-[9px] font-medium uppercase tracking-[0.16em] text-bone/60">
                Offline · cached view
              </span>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {updateReady && (
            <motion.div
              role="status"
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
              className="pointer-events-auto w-full overflow-hidden rounded-[8px] border border-projector/25 bg-carbon/95 shadow-[0_18px_44px_rgba(0,0,0,0.65)] backdrop-blur-2xl"
            >
              <div className="flex items-center gap-3 p-2.5 pl-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[5px] bg-projector/12 text-projector">
                  <FiRefreshCw className="text-[15px]" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="timecode text-[9px] uppercase tracking-[0.18em] text-projector/70">
                    New cut
                  </p>
                  <p className="mt-0.5 text-[13px] font-semibold leading-tight text-bone">
                    A fresh version is ready
                  </p>
                </div>
                <button
                  type="button"
                  onClick={applyUpdate}
                  className="shrink-0 rounded-[4px] bg-projector px-3 py-2 text-[11px] font-bold text-ink transition-all hover:bg-[#F0CA98] active:scale-95"
                >
                  Reload
                </button>
                <button
                  type="button"
                  onClick={() => setUpdateReady(false)}
                  aria-label="Dismiss update"
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[4px] text-bone/55 transition-colors hover:bg-bone/[0.06] hover:text-bone/70"
                >
                  <FiX className="text-sm" />
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Install sheet, floating above the mobile dock */}
      <AnimatePresence>
        {installSheetOpen && (
          <motion.div
            role="dialog"
            aria-label={`Install ${SITENAME}`}
            initial={{ y: 120, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 120, opacity: 0 }}
            transition={{ type: "spring", stiffness: 300, damping: 30 }}
            className="fixed inset-x-3 bottom-[calc(var(--safe-bottom)+var(--mob-dock-footprint,73px)+4.5rem)] z-[55] md:inset-x-auto md:bottom-6 md:right-6 md:w-[360px]"          >
            <div className="relative overflow-hidden rounded-[8px] border border-bone/10 bg-carbon/95 shadow-[0_26px_64px_rgba(0,0,0,0.72)] backdrop-blur-2xl">
              <div
                aria-hidden
                className="absolute inset-x-0 top-0 h-px"
                style={{
                  background:
                    "linear-gradient(90deg, transparent, rgba(233,184,114,0.6), transparent)",
                }}
              />

              <button
                type="button"
                onClick={() => dismissInstall("close")}
                aria-label="Dismiss install prompt"
                className="absolute right-3 top-3 z-10 flex h-7 w-7 items-center justify-center rounded-[4px] text-bone/55 transition-colors hover:bg-bone/[0.06] hover:text-bone/70"
              >
                <FiX className="text-sm" />
              </button>

              <div className="p-4 pr-10">
                <div className="mb-3 flex items-center gap-3">
                  <img
                    src="/icons/icon-192x192.png"
                    alt=""
                    width="44"
                    height="44"
                    className="h-11 w-11 shrink-0 rounded-[10px] ring-1 ring-bone/10"
                  />
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-bold leading-tight text-bone">
                      {iosOpen ? "Add to Home Screen" : `Install ${SITENAME}`}
                    </h3>
                    <p className="mt-0.5 text-[11px] text-bone/60">
                      {iosOpen
                        ? "Two taps in Safari — no app store."
                        : "Full-screen playback, one tap away."}
                    </p>
                  </div>
                </div>

                {iosOpen ? (
                  <>
                    <ol className="mb-4 space-y-2.5 px-1">
                      {[
                        {
                          step: "01",
                          icon: FiShare,
                          copy: "Tap Share in Safari's toolbar.",
                        },
                        {
                          step: "02",
                          icon: FiPlusSquare,
                          copy: 'Choose "Add to Home Screen".',
                        },
                        {
                          step: "03",
                          icon: FiSmartphone,
                          copy: "Tap Add — the app lands on your home screen.",
                        },
                      ].map(({ step, icon: Icon, copy }) => (
                        <li key={step} className="flex items-center gap-3">
                          <span className="timecode text-[9px] font-medium tracking-[0.12em] text-projector/70">
                            {step}
                          </span>
                          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[4px] bg-bone/[0.05] text-bone/60">
                            <Icon className="text-[11px]" />
                          </span>
                          <span className="text-[11.5px] leading-snug text-bone/55">{copy}</span>
                        </li>
                      ))}
                    </ol>
                    <button
                      type="button"
                      onClick={() => dismissInstall("ios_guide_ack")}
                      className="w-full rounded-[4px] bg-projector py-2.5 text-xs font-bold text-ink shadow-[0_4px_14px_rgba(233,184,114,0.25)] transition-all hover:bg-[#F0CA98] active:scale-[0.98]"
                    >
                      Got it
                    </button>
                  </>
                ) : (
                  <>
                    <div className="mb-4 flex items-center gap-3 px-1">
                      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-bone/60">
                        <MdOutlineOfflineBolt className="text-sm text-projector" />
                        Offline shell
                      </div>
                      <div className="h-3 w-px bg-bone/10" />
                      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-bone/60">
                        <FiSmartphone className="text-xs text-projector" />
                        Full-screen
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => dismissInstall("not_now")}
                        className="flex-1 rounded-[4px] border border-bone/10 bg-bone/[0.03] py-2.5 text-xs font-semibold text-bone/50 transition-colors hover:border-bone/20 hover:bg-bone/[0.06] hover:text-bone/80"
                      >
                        Not now
                      </button>
                      <button
                        type="button"
                        onClick={handleInstall}
                        disabled={installing}
                        className="flex-1 rounded-[4px] bg-projector py-2.5 text-xs font-bold text-ink shadow-[0_4px_14px_rgba(233,184,114,0.25)] transition-all hover:bg-[#F0CA98] active:scale-[0.97] disabled:opacity-60"
                      >
                        {installing ? (
                          <div className="mx-auto h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink/30 border-t-ink" />
                        ) : (
                          <span className="flex items-center justify-center gap-1.5">
                            <FiDownload className="text-sm" />
                            Install
                          </span>
                        )}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
