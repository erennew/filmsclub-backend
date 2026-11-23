import React, { useEffect, useRef, useState } from "react";
import { FiEye, FiEyeOff, FiLock, FiArrowRight, FiSettings, FiCheck } from "react-icons/fi";
import { login, getAdminBase, setAdminBase, clearAdminBase, getSavedAdminBase, clearToken } from "./api";
import { SITENAME } from "../config";
import { Button, Label, Spinner, TextInput } from "./ui";

export default function LoginPage({ onSuccess }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPass, setShowPass] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showConn, setShowConn] = useState(
    () => typeof window !== "undefined" && new URLSearchParams(window.location.search).has("conn")
  );
  const [apiUrl, setApiUrl] = useState(() => getSavedAdminBase() || getAdminBase());
  const [connSaved, setConnSaved] = useState(false);
  const userRef = useRef(null);

  useEffect(() => {
    userRef.current?.focus();
  }, []);

  const saveConn = () => {
    const previous = getAdminBase();
    const base = setAdminBase(apiUrl);
    // A session issued by one backend must not be sent to another.
    if (previous && base && previous !== base) clearToken();
    setApiUrl(base || getAdminBase());
    setConnSaved(true);
    window.setTimeout(() => setConnSaved(false), 1800);
  };

  const resetConn = () => {
    const previous = getAdminBase();
    clearAdminBase();
    if (previous) clearToken();
    setApiUrl(getAdminBase());
    setConnSaved(true);
    window.setTimeout(() => setConnSaved(false), 1800);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!username || !password) {
      setError("Enter your username and password.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await login(username, password);
      onSuccess();
    } catch (err) {
      setError(
        err.status === 429
          ? "Too many attempts — wait a few minutes and try again."
          : err.status === 401
            ? "Invalid username or password."
            : err.message
      );
      // A connection failure usually means the API URL is wrong.
      if (err.status === 0) setShowConn(true);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-ink text-bone relative overflow-hidden flex items-center justify-center px-4 py-10">
      {/* projector beam + stage glow */}
      <div
        aria-hidden="true"
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(1100px 480px at 50% -8%, rgba(233,184,114,0.13), transparent 62%)," +
            "radial-gradient(700px 420px at 85% 100%, rgba(255,90,60,0.06), transparent 60%)," +
            "linear-gradient(180deg, #0a0b0e 0%, #08090b 55%, #060709 100%)",
        }}
      />
      {/* faint film grain dots */}
      <div
        aria-hidden="true"
        className="absolute inset-0 pointer-events-none opacity-[0.05]"
        style={{
          backgroundImage: "radial-gradient(rgba(242,237,227,0.6) 0.6px, transparent 0.6px)",
          backgroundSize: "22px 22px",
        }}
      />

      <main className="relative w-full max-w-[400px]">
        <header className="text-center mb-8">
          <p className="timecode text-[10px] uppercase tracking-[0.34em] text-projector/80 mb-3">
            Reel Control Room
          </p>
          <h1 className="font-display text-4xl font-medium leading-tight">{SITENAME}</h1>
          <p className="text-sm text-bone/50 mt-2">Admin console — staff only.</p>
        </header>

        <form
          onSubmit={submit}
          className="glass rounded-xl p-6 sm:p-7 shadow-[0_40px_90px_-40px_rgba(0,0,0,0.95)]"
        >
          <div className="mb-5">
            <Label htmlFor="admin-user">Username</Label>
            <TextInput
              id="admin-user"
              ref={userRef}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="admin"
            />
          </div>

          <div className="mb-6">
            <Label htmlFor="admin-pass">Password</Label>
            <div className="relative">
              <TextInput
                id="admin-pass"
                type={showPass ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="••••••••"
                className="pr-11"
              />
              <button
                type="button"
                aria-label={showPass ? "Hide password" : "Show password"}
                onClick={() => setShowPass((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-bone/55 hover:text-bone/80 transition-colors"
              >
                {showPass ? <FiEyeOff size={16} /> : <FiEye size={16} />}
              </button>
            </div>
          </div>

          {error && (
            <p
              role="alert"
              className="mb-5 rounded-md border border-signal/40 bg-signal/8 px-3.5 py-2.5 text-sm text-signal leading-snug"
            >
              {error}
            </p>
          )}

          <Button
            type="submit"
            variant="primary"
            disabled={busy}
            className="w-full !py-3 rounded-md tracking-wide"
          >
            {busy ? (
              <>
                <Spinner className="border-ink/25 border-t-ink" /> Checking reel…
              </>
            ) : (
              <>
                Enter console <FiArrowRight />
              </>
            )}
          </Button>

          <p className="mt-5 flex items-center justify-center gap-1.5 timecode text-[10px] uppercase tracking-[0.14em] text-bone/55">
            <FiLock size={11} /> Session stays in this tab only
          </p>
        </form>

        <div className="mt-4 text-center">
          <button
            type="button"
            onClick={() => setShowConn((v) => !v)}
            className="inline-flex items-center gap-1.5 text-[11px] text-bone/60 hover:text-bone/80 transition-colors"
          >
            <FiSettings size={13} /> Backend connection
            {getSavedAdminBase() ? <span className="text-projector">· custom</span> : null}
          </button>
          {showConn ? (
            <div className="mt-3 glass rounded-xl p-4 text-left">
              <Label htmlFor="admin-api-url">Backend API URL</Label>
              <TextInput
                id="admin-api-url"
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder="https://api.example.com"
                autoCapitalize="none"
                spellCheck={false}
                className="font-mono text-[12px]"
              />
              <p className="text-[11px] text-bone/55 leading-snug mt-2">
                The console needs the backend origin (not the website). If every
                page is empty or login fails, this URL is usually the cause. It is
                stored only in this browser.
              </p>
              <div className="flex gap-2 mt-3">
                <Button variant="primary" onClick={saveConn} className="flex-1">
                  {connSaved ? <FiCheck size={14} /> : null} {connSaved ? "Saved" : "Save URL"}
                </Button>
                <Button variant="ghost" onClick={resetConn}>
                  Use default
                </Button>
              </div>
            </div>
          ) : null}
        </div>

        <p className="text-center text-[11px] text-bone/50 mt-6">
          Wrong password 8 times in 5 minutes locks the attempt window.
        </p>
      </main>
    </div>
  );
}
