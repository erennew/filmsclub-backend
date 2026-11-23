/* Shared UI primitives for the admin console — same film-noir language as the site. */
import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { FiX, FiCheck, FiAlertTriangle, FiInfo } from "react-icons/fi";
import { clearToken, isStaleBackendError } from "./api";

export const cx = (...parts) => parts.filter(Boolean).join(" ");

export function formatEta(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s <= 0) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  const sec = Math.floor(s % 60);
  return m ? `${m}m ${sec}s` : `${sec}s`;
}

/* ── Toasts ─────────────────────────────────────────────────────────────── */
const toastListeners = new Set();
let toastSeq = 0;

export function toast(message, kind = "success") {
  toastListeners.forEach((fn) => fn({ id: ++toastSeq, message, kind }));
}

export function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const add = (item) => {
      setItems((prev) => [...prev, item]);
      setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== item.id)), 3200);
    };
    toastListeners.add(add);
    return () => toastListeners.delete(add);
  }, []);
  if (!items.length) return null;
  return createPortal(
    <div className="fixed top-4 right-4 z-[80] flex flex-col gap-2 max-w-sm">
      {items.map((t) => (
        <div
          key={t.id}
          role="status"
          className={cx(
            "glass-dark flex items-start gap-2.5 rounded-md px-4 py-3 text-sm shadow-[0_16px_40px_-16px_rgba(0,0,0,0.9)]",
            t.kind === "error" ? "text-signal" : t.kind === "info" ? "text-bone/80" : "text-projector"
          )}
        >
          <span className="mt-0.5 shrink-0">
            {t.kind === "error" ? <FiAlertTriangle /> : t.kind === "info" ? <FiInfo /> : <FiCheck />}
          </span>
          <span className="leading-snug text-bone/90">{t.message}</span>
        </div>
      ))}
    </div>,
    document.body
  );
}

/* ── Building blocks ────────────────────────────────────────────────────── */
export function Panel({ title, subtitle, actions, children, className }) {
  return (
    <section className={cx("glass-card rounded-lg p-4 sm:p-5", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center gap-3 mb-4">
          <div className="min-w-0 mr-auto">
            {title && <h2 className="section-title text-lg text-bone">{title}</h2>}
            {subtitle && (
              <p className="timecode text-[10px] uppercase tracking-[0.16em] text-bone/50 mt-0.5">
                {subtitle}
              </p>
            )}
          </div>
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Label({ children, htmlFor }) {
  return (
    <label
      htmlFor={htmlFor}
      className="block timecode text-[10px] uppercase tracking-[0.16em] text-bone/50 mb-1.5"
    >
      {children}
    </label>
  );
}

const inputBase =
  "w-full rounded-md bg-ink/60 border border-bone/12 px-3 py-2.5 text-sm text-bone placeholder:text-bone/50 focus:outline-none focus:border-projector/60 focus:ring-1 focus:ring-projector/30 transition-colors";

export const TextInput = React.forwardRef(function TextInput(props, ref) {
  return <input ref={ref} {...props} className={cx(inputBase, props.className)} />;
});

export function TextArea(props) {
  return <textarea {...props} className={cx(inputBase, "min-h-[90px] leading-relaxed", props.className)} />;
}

export function Select({ children, ...props }) {
  return (
    <select {...props} className={cx(inputBase, "appearance-none", props.className)}>
      {children}
    </select>
  );
}

export function Field({ label, hint, children }) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
      {hint && <p className="text-[11px] text-bone/60 mt-1">{hint}</p>}
    </div>
  );
}

export function Button({ variant = "ghost", className, children, ...props }) {
  const styles = {
    primary:
      "bg-projector text-ink font-semibold hover:bg-projector/85 active:scale-[0.98] shadow-[0_10px_24px_-12px_rgba(233,184,114,0.8)]",
    ghost:
      "bg-bone/5 text-bone/85 border border-bone/12 hover:bg-bone/10 hover:text-bone active:scale-[0.98]",
    danger:
      "bg-signal/12 text-signal border border-signal/40 hover:bg-signal/20 active:scale-[0.98]",
    quiet: "text-bone/55 hover:text-bone",
  }[variant];
  return (
    <button
      {...props}
      type={props.type || "button"}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm transition-all duration-150 disabled:opacity-40 disabled:pointer-events-none",
        styles,
        className
      )}
    >
      {children}
    </button>
  );
}

export function Badge({ tone = "neutral", children }) {
  const tones = {
    neutral: "border-bone/15 text-bone/60",
    good: "border-projector/40 text-projector",
    ok: "border-emerald-400/35 text-emerald-300",
    warn: "border-amber-300/35 text-amber-200",
    bad: "border-signal/45 text-signal",
  };
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 timecode text-[10px] uppercase tracking-[0.12em] px-2 py-0.5 rounded-[2px] border bg-ink/50",
        tones[tone]
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ className }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={cx(
        "inline-block w-4 h-4 rounded-full border-2 border-bone/20 border-t-projector animate-spin align-middle",
        className
      )}
    />
  );
}

export function EmptyState({ icon, title, children }) {
  return (
    <div className="text-center py-10 px-4">
      {icon && <div className="text-3xl text-bone/50 mb-3 flex justify-center">{icon}</div>}
      <p className="section-title text-bone/60 text-base">{title}</p>
      {children && <p className="text-sm text-bone/55 mt-1 max-w-sm mx-auto">{children}</p>}
    </div>
  );
}

/* One shared error state for every admin page. A 404 from /admin/api/* is not a
   broken page: the running backend predates those routes (or the console is
   pointed at the frontend). Explain the real fix once, with actions, instead of
   showing "Not Found" on six separate pages. */
export function AdminErrorState({ title, error }) {
  const message =
    typeof error === "string" ? error : (error && error.message) || String(error || "");
  if (!isStaleBackendError(error)) {
    return <EmptyState title={title}>{message}</EmptyState>;
  }
  return (
    <div className="text-left py-7 px-2 max-w-2xl mx-auto">
      <Badge tone="bad">Backend out of date</Badge>
      <p className="section-title text-bone/80 text-lg mt-3">
        {title} — the running backend is an older build
      </p>
      <p className="text-sm text-bone/55 mt-2 leading-relaxed">
        The console asked for an admin route and got <code>404 Not Found</code>.
        Manual add, multi-channel indexing, the skip-count prompt, hero,
        indexing, orphans, curation, settings and audit all exist in the current
        source — they are missing here only because the backend was not
        redeployed.
      </p>
      <ol className="text-sm text-bone/55 mt-3 list-decimal pl-5 space-y-1.5 leading-relaxed">
        <li>
          Deploy or restart the current <code>filmsclub-backend</code> (a
          frontend-only redeploy cannot add backend routes).
        </li>
        <li>
          If it is already current, check the backend URL — the console may be
          talking to the frontend or a different host.
        </li>
      </ol>
      <div className="flex flex-wrap gap-2 mt-5">
        <Button variant="primary" onClick={() => window.location.reload()}>
          Recheck
        </Button>
        <Button
          onClick={() => {
            clearToken();
            window.location.href = `${window.location.pathname}?conn=1`;
          }}
        >
          Backend connection
        </Button>
      </div>
      {message ? (
        <details className="mt-5">
          <summary className="text-[12px] text-bone/55 cursor-pointer select-none">
            Technical detail
          </summary>
          <p className="timecode text-[10px] text-bone/55 mt-2 break-all leading-relaxed">
            {message}
          </p>
        </details>
      ) : null}
    </div>
  );
}

export function Modal({ open, onClose, title, children, width = "max-w-lg" }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-ink/80 backdrop-blur-sm"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div role="dialog" aria-modal="true" className={cx("glass-dark w-full rounded-lg shadow-2xl max-h-[86vh] flex flex-col", width)}>
        <header className="flex items-center gap-3 px-5 py-4 border-b border-bone/8 shrink-0">
          <h3 className="section-title text-lg text-bone mr-auto">{title}</h3>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="text-bone/50 hover:text-bone transition-colors"
          >
            <FiX size={18} />
          </button>
        </header>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>,
    document.body
  );
}

export function ConfirmModal({ open, onClose, title, confirmWord, onConfirm, confirmLabel = "Confirm", busy }) {
  const [word, setWord] = useState("");
  useEffect(() => {
    if (open) setWord("");
  }, [open]);
  const ready = !confirmWord || word.trim().toUpperCase() === confirmWord;
  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-md">
      {confirmWord && (
        <div className="mb-4">
          <Label>Type “{confirmWord}” to confirm</Label>
          <TextInput value={word} onChange={(e) => setWord(e.target.value)} placeholder={confirmWord} autoFocus />
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="danger"
          disabled={!ready || busy}
          onClick={() => onConfirm()}
        >
          {busy ? <Spinner /> : null}
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
