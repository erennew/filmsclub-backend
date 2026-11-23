import React from "react";

/**
 * ErrorBoundary — stops a single render throw from blanking the whole app.
 *
 * Without this, one bad record (e.g. a `rating` stored as a string, which makes
 * `rating?.toFixed()` throw) leaves PWA users on a permanent white screen with
 * no nav and no way back.
 *
 * Usage:
 *   <ErrorBoundary>            // app-level, full-page recovery
 *   <ErrorBoundary compact>    // route-level, keeps nav + footer visible
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
    this.handleReload = this.handleReload.bind(this);
    this.handleReset = this.handleReset.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Keep the detail in the console for support; the UI stays human-readable.
    // eslint-disable-next-line no-console
    console.error("Render error caught by ErrorBoundary:", error, info?.componentStack);
  }

  handleReload() {
    window.location.reload();
  }

  handleReset() {
    this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const compact = this.props.compact;
    const message =
      (error && (error.message || String(error))) || "Unknown error";

    return (
      <div
        role="alert"
        className={
          compact
            ? "flex items-center justify-center px-4 py-16"
            : "min-h-screen bg-ink flex items-center justify-center px-5 py-16"
        }
      >
        <div className="w-full max-w-md text-center">
          <p className="timecode text-[10px] uppercase tracking-[0.22em] text-projector/80">
            Projection interrupted
          </p>
          <h1 className="font-display text-2xl sm:text-3xl text-bone mt-2.5">
            Something broke in the projector
          </h1>
          <p className="text-sm text-bone/60 mt-3 leading-relaxed">
            This part of the page hit an unexpected error. Your watchlist and
            history are stored on this device and are untouched.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-2.5 mt-6">
            {compact && (
              <button
                type="button"
                onClick={this.handleReset}
                className="inline-flex items-center gap-2 rounded-[3px] border border-bone/15 bg-bone/5 px-4 py-2.5 text-sm font-medium text-bone/85 transition-colors hover:bg-bone/10 hover:text-bone focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
              >
                Try again
              </button>
            )}
            <button
              type="button"
              onClick={this.handleReload}
              className="inline-flex items-center gap-2 rounded-[3px] bg-projector px-4 py-2.5 text-sm font-bold text-ink transition-colors hover:bg-[#F0CA98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
            >
              Reload the page
            </button>
            <a
              href="/"
              className="inline-flex items-center gap-2 rounded-[3px] px-3 py-2.5 text-sm font-medium text-bone/60 transition-colors hover:text-bone focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
            >
              Go home
            </a>
          </div>

          {import.meta.env.DEV && (
            <details className="mt-8 text-left">
              <summary className="timecode cursor-pointer select-none text-[11px] text-bone/60">
                Technical detail (dev only)
              </summary>
              <pre className="timecode mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-[3px] border border-bone/10 bg-carbon p-3 text-[11px] text-bone/60">
                {message}
              </pre>
            </details>
          )}
        </div>
      </div>
    );
  }
}
