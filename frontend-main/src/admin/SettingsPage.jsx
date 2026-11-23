import React, { useCallback, useEffect, useState } from "react";
import { FiRefreshCw, FiRotateCcw, FiSave } from "react-icons/fi";
import { api } from "./api";
import { AdminErrorState, Badge, Button, Label, Panel, Spinner, TextInput, cx, toast } from "./ui";

/* Live runtime knobs. An override survives a restart; clearing it falls back
   to the value the environment defines. */
export default function SettingsPage() {
  const [knobs, setKnobs] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState({});

  const load = useCallback(async () => {
    try {
      const data = await api.get("/admin/api/config");
      const rows = data.knobs || [];
      setKnobs(rows);
      setDrafts(
        rows.reduce((acc, row) => {
          acc[row.key] = row.type === "bool" ? String(row.value) : String(row.value ?? "");
          return acc;
        }, {})
      );
      setError("");
      setLoaded(true);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = async (row) => {
    const raw = drafts[row.key];

    /*
     * Coerce before sending. The old line was
     *   row.type === "int" ? Number(raw) : Number(raw)
     * — both branches identical — so every non-bool knob went through
     * `Number(raw)`. Clearing a field produced Number("") === 0, which is a
     * valid in-range integer for most knobs, so emptying an input and pressing
     * Save silently wrote 0 instead of complaining. Non-numeric text produced
     * NaN, which JSON-serialises to null and came back as a confusing
     * "'key' expects an integer".
     */
    let value;
    if (row.type === "bool") {
      value = raw === "true";
    } else {
      const trimmed = String(raw ?? "").trim();
      if (trimmed === "") {
        toast(`${row.label} needs a value`, "error");
        return;
      }
      value = Number(trimmed);
      if (!Number.isFinite(value)) {
        toast(`${row.label} must be a number`, "error");
        return;
      }
      if (row.type === "int" && !Number.isInteger(value)) {
        toast(`${row.label} must be a whole number`, "error");
        return;
      }
      if (row.min != null && value < row.min) {
        toast(`${row.label} must be at least ${row.min}`, "error");
        return;
      }
      if (row.max != null && value > row.max) {
        toast(`${row.label} must be at most ${row.max}`, "error");
        return;
      }
    }

    setBusy(true);
    try {
      await api.patch("/admin/api/config", { key: row.key, value });
      toast(`${row.label} saved`);
      await load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const reset = async (row) => {
    setBusy(true);
    try {
      await api.patch("/admin/api/config", { key: row.key, reset: true });
      toast(`${row.label} reset to the environment default`);
      await load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  if (error && !loaded)
    return (
      <Panel>
        <AdminErrorState title="Settings unavailable" error={error} />
      </Panel>
    );
  if (!loaded)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Operations</p>
          <h1 className="font-display text-3xl mt-1.5">Runtime settings</h1>
          <p className="text-sm text-bone/60 mt-1">
            Live queue and index knobs. Every change is audited.
          </p>
        </div>
        <Button variant="ghost" onClick={load} disabled={busy}>
          <FiRefreshCw size={14} /> Refresh
        </Button>
      </header>

      <Panel title="Knobs" subtitle="Overrides are stored in app_settings and survive a restart">
        <div className="space-y-3">
          {knobs.map((row) => (
            <div
              key={row.key}
              className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] items-end rounded-md border border-bone/8 bg-ink/40 px-4 py-3.5"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-bone">{row.label}</p>
                  {row.overridden ? (
                    <Badge tone="good">Overridden</Badge>
                  ) : (
                    <Badge>Default</Badge>
                  )}
                  {row.restart_only ? <Badge tone="warn">Restart only</Badge> : null}
                </div>
                <p className="timecode text-[10px] text-bone/60 mt-1">
                  {row.key} · {row.live}
                </p>
                <p className="timecode text-[10px] text-bone/55 mt-0.5">
                  env default: {String(row.default)}
                  {row.min != null && row.max != null ? ` · range ${row.min}–${row.max}` : ""}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {row.type === "bool" ? (
                  <select
                    aria-label={row.label}
                    value={drafts[row.key] ?? "false"}
                    disabled={busy}
                    onChange={(e) => setDrafts((d) => ({ ...d, [row.key]: e.target.value }))}
                    className="rounded-md bg-ink/60 border border-bone/12 px-3 py-2 text-sm text-bone focus:outline-none focus:border-projector/60"
                  >
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                  </select>
                ) : (
                  <TextInput
                    aria-label={row.label}
                    type="number"
                    step={row.type === "float" ? "0.1" : "1"}
                    min={row.min ?? undefined}
                    max={row.max ?? undefined}
                    value={drafts[row.key] ?? ""}
                    disabled={busy}
                    onChange={(e) => setDrafts((d) => ({ ...d, [row.key]: e.target.value }))}
                    className="w-32 text-right tabular-nums"
                  />
                )}
                <Button variant="primary" disabled={busy} onClick={() => save(row)}>
                  <FiSave size={14} /> Save
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy || !row.overridden}
                  onClick={() => reset(row)}
                  title="Drop the override and fall back to the environment default"
                >
                  <FiRotateCcw size={14} />
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <p className={cx("text-[11px] text-bone/55")}>
        A knob left at its default reproduces the behaviour the bot had before
        this page existed. “Restart only” means the value is applied when the
        process starts, not while it runs.
      </p>
    </div>
  );
}
