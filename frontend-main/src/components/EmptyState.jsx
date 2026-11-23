import React from "react";
import { FiFilm, FiRefreshCw } from "react-icons/fi";

export default function EmptyState({
  icon: Icon = FiFilm,
  eyebrow,
  title,
  description,
  action,
  className = "",
}) {
  return (
    <div className={`flex flex-col items-center justify-center px-6 py-20 text-center ${className}`}>
      <div className="relative mb-5">
        <div
          aria-hidden
          className="absolute -inset-4 rounded-full bg-projector/10 blur-2xl"
        />
        <div className="relative flex h-16 w-16 items-center justify-center rounded-full border border-projector/20 bg-carbon shadow-[inset_0_1px_0_rgba(242,237,227,0.06)]">
          <Icon className="text-2xl text-projector/75" />
        </div>
        <span
          aria-hidden
          className="absolute inset-x-0 -bottom-1 h-px bg-gradient-to-r from-transparent via-projector/40 to-transparent"
        />
      </div>
      {eyebrow && <span className="eyebrow mb-2">{eyebrow}</span>}
      <h2 className="font-display text-xl text-bone/90 sm:text-2xl">{title}</h2>
      {description && (
        <p className="mt-2 max-w-sm text-sm leading-relaxed text-bone/50">
          {description}
        </p>
      )}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}

export function RetryButton({ onClick, label = "Try again" }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group inline-flex items-center gap-2 rounded-[3px] border border-projector/35 bg-projector/10 px-4 py-2.5 text-xs font-bold text-projector transition-all duration-200 hover:border-projector/60 hover:bg-projector/15 active:scale-95"
    >
      <FiRefreshCw className="text-sm transition-transform duration-500 group-hover:rotate-180" />
      {label}
    </button>
  );
}
