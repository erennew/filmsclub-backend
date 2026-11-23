import React, { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import {
  FiActivity,
  FiDatabase,
  FiGrid,
  FiHardDrive,
  FiLayers,
  FiList,
  FiLogOut,
  FiMenu,
  FiPlus,
  FiTerminal,
  FiSettings,
  FiShield,
  FiSun,
  FiSearch,
  FiX,
} from "react-icons/fi";
import {
  api,
  checkBackendCompatibility,
  clearToken,
  EXPECTED_ADMIN_BUILD,
  getToken,
  logout,
  onAuthEvent,
  REQUIRED_ADMIN_ROUTES,
} from "./api";
import { SITENAME } from "../config";
import { Badge, Button, Spinner, Toaster, cx } from "./ui";
import LoginPage from "./LoginPage";
import DashboardPage from "./DashboardPage";
import MediaLibraryPage from "./MediaLibraryPage";
import MediaEditorPage from "./MediaEditorPage";
import ManualAddPage from "./ManualAddPage";
import HeroPage from "./HeroPage";
import QueuePage from "./QueuePage";
import FailedFilesPage from "./FailedFilesPage";
import CommandsPage from "./CommandsPage";
import CachePage from "./CachePage";
import HealthPage from "./HealthPage";
import SettingsPage from "./SettingsPage";
import AuditPage from "./AuditPage";
import IndexingPage from "./IndexingPage";
import CurationPage from "./CurationPage";
import OrphansPage from "./OrphansPage";

const NAV = [
  { to: "/admin", end: true, label: "Dashboard", icon: FiGrid },
  { to: "/admin/media", label: "Media library", icon: FiList },
  { to: "/admin/add", label: "Manual add", icon: FiPlus },
  { to: "/admin/hero", label: "Hero carousel", icon: FiSun },
  { to: "/admin/queue", label: "Queue", icon: FiActivity },
  { to: "/admin/failed", label: "Failed files", icon: FiX },
  { to: "/admin/indexing", label: "Indexing", icon: FiSearch },
  { to: "/admin/curation", label: "Curation", icon: FiLayers },
  { to: "/admin/orphans", label: "Orphans", icon: FiHardDrive },
  { to: "/admin/commands", label: "Commands", icon: FiTerminal },
  { to: "/admin/cache", label: "Cache", icon: FiDatabase },
  { to: "/admin/health", label: "Health", icon: FiHardDrive },
  { to: "/admin/settings", label: "Settings", icon: FiSettings },
  { to: "/admin/audit", label: "Audit", icon: FiShield },
];

function AuthGate({ children }) {
  const [phase, setPhase] = useState(getToken() ? "validating" : "anonymous");

  useEffect(() => {
    let alive = true;
    const validate = () => {
      if (!getToken()) {
        setPhase("anonymous");
        return;
      }
      setPhase("validating");
      api
        .get("/admin/api/session")
        .then((d) => {
          if (!alive) return;
          if (d.authenticated) setPhase("authed");
          else {
            clearToken();
            setPhase("anonymous");
          }
        })
        .catch(() => {
          if (!alive) return;
          clearToken();
          setPhase("anonymous");
        });
    };
    validate();
    const off = onAuthEvent((type) => {
      if (!alive) return;
      if (type === "signed-in") setPhase("authed");
      if (type === "signed-out" || type === "expired") {
        clearToken();
        setPhase("anonymous");
      }
    });
    return () => {
      alive = false;
      off();
    };
  }, []);

  if (phase === "validating")
    return (
      <div className="min-h-screen bg-ink flex items-center justify-center">
        <Spinner className="w-6 h-6" />
      </div>
    );
  if (phase === "anonymous") return <LoginPage onSuccess={() => setPhase("authed")} />;
  return children;
}

/* A connected backend that predates the current console returns "Not Found"
   for the newer pages. Detect that once after login and explain the actual fix
   (deploy/restart the backend) instead of showing six cryptic page errors. */
function BackendCompatibilityBanner() {
  const [state, setState] = useState(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    checkBackendCompatibility()
      .then((info) => {
        if (alive) setState(info.outdated ? info : null);
      })
      .catch((e) => {
        if (!alive) return;
        setState({
          outdated: true,
          build: "unknown",
          version: "unknown",
          missing: REQUIRED_ADMIN_ROUTES,
          message: e.message,
        });
      });
    return () => {
      alive = false;
    };
  }, [attempt]);

  if (!state) return null;
  return (
    <div className="mb-5 rounded-md border border-signal/40 bg-signal/10 px-4 py-3.5">
      <div className="flex flex-wrap items-start gap-3">
        <div className="mr-auto min-w-0">
          <p className="text-sm font-medium text-signal">Backend build is out of date</p>
          <p className="text-[12px] text-bone/65 mt-1">
            This console expects build <code>{EXPECTED_ADMIN_BUILD}</code> but is
            talking to <code>{state.build}</code> (v{state.version}). Deploy or
            restart the current backend, then recheck.{" "}
            {state.message || ""}
          </p>
          {state.missing?.length > 0 ? (
            <p className="timecode text-[10px] text-bone/60 mt-2 break-all">
              Missing routes: {state.missing.join(", ")}
            </p>
          ) : null}
        </div>
        <Button
          variant="ghost"
          onClick={() => {
            setState(null);
            setAttempt((n) => n + 1);
          }}
        >
          Recheck
        </Button>
      </div>
    </div>
  );
}

function Sidebar({ open, onClose }) {
  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-ink/70 backdrop-blur-sm md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={cx(
          "fixed z-50 inset-y-0 left-0 w-[248px] glass-dark flex flex-col transition-transform duration-200 md:translate-x-0 md:sticky md:top-0 md:h-screen md:z-10",
          open ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="px-5 pt-6 pb-5 border-b border-bone/6">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-projector shadow-[0_0_10px_rgba(233,184,114,0.9)]" />
            <p className="font-display text-xl leading-none">{SITENAME}</p>
            <button
              type="button"
              aria-label="Close menu"
              className="ml-auto text-bone/50 hover:text-bone md:hidden"
              onClick={onClose}
            >
              <FiX size={18} />
            </button>
          </div>
          <p className="timecode text-[9px] uppercase tracking-[0.3em] text-bone/55 mt-2">
            Admin console
          </p>
        </div>

        <nav className="flex-1 overflow-y-auto py-3 px-3">
          {NAV.map(({ to, end, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onClose}
              className={({ isActive }) =>
                cx(
                  "group relative flex items-center gap-3 rounded-md px-3 py-2.5 mb-0.5 text-sm transition-colors",
                  isActive ? "bg-bone/8 text-bone" : "text-bone/50 hover:text-bone hover:bg-bone/4"
                )
              }
            >
              {({ isActive }) => (
                <>
                  <span
                    className={cx(
                      "absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[2px] rounded-full bg-projector transition-opacity",
                      isActive ? "opacity-100" : "opacity-0"
                    )}
                  />
                  <Icon size={15} className={isActive ? "text-projector" : ""} />
                  <span className={cx("font-medium", isActive && "text-bone")}>{label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="px-4 py-4 border-t border-bone/6 space-y-2">
          <button
            type="button"
            onClick={() => {
              clearToken();
              window.location.href = `${window.location.pathname}?conn=1`;
            }}
            className="flex items-center gap-2.5 text-sm text-bone/50 hover:text-bone transition-colors w-full"
          >
            <FiSettings size={15} /> Backend connection
          </button>
          <button
            type="button"
            onClick={() => logout()}
            className="flex items-center gap-2.5 text-sm text-bone/50 hover:text-signal transition-colors w-full"
          >
            <FiLogOut size={15} /> Sign out
          </button>
        </div>
      </aside>
    </>
  );
}

export default function AdminShell() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <AuthGate>
      <div className="min-h-screen bg-ink text-bone flex">
        <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
        <div className="flex-1 min-w-0">
          {/* mobile top bar */}
          <header className="sticky top-0 z-20 md:hidden glass-dark flex items-center gap-3 px-4 py-3 border-b border-bone/6">
            <button
              type="button"
              aria-label="Open menu"
              className="text-bone/70 hover:text-bone"
              onClick={() => setMenuOpen(true)}
            >
              <FiMenu size={20} />
            </button>
            <p className="font-display text-lg mr-auto">{SITENAME}</p>
            <Badge tone="good">Admin</Badge>
          </header>
          <main className="px-4 sm:px-6 lg:px-8 py-6 max-w-[1500px] mx-auto w-full">
            <BackendCompatibilityBanner />
            <Routes>
              <Route index element={<DashboardPage />} />
              <Route path="media" element={<MediaLibraryPage />} />
              <Route path="media/:type/:dbIndex/:tmdbId" element={<MediaEditorPage />} />
              <Route path="add" element={<ManualAddPage />} />
              <Route path="hero" element={<HeroPage />} />
              <Route path="queue" element={<QueuePage />} />
              <Route path="failed" element={<FailedFilesPage />} />
              <Route path="indexing" element={<IndexingPage />} />
              <Route path="curation" element={<CurationPage />} />
              <Route path="orphans" element={<OrphansPage />} />
              <Route path="commands" element={<CommandsPage />} />
              <Route path="cache" element={<CachePage />} />
              <Route path="health" element={<HealthPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="audit" element={<AuditPage />} />
              <Route
                path="*"
                element={
                  <div className="rounded-xl border border-bone/10 bg-carbon/30 p-8 text-center">
                    <p className="eyebrow mb-2">404</p>
                    <h2 className="font-display text-xl text-bone/90 mb-3">No such admin page</h2>
                    <NavLink to="/admin" className="text-projector hover:underline">
                      Back to dashboard
                    </NavLink>
                  </div>
                }
              />
            </Routes>
          </main>
        </div>
      </div>
      <Toaster />
    </AuthGate>
  );
}
