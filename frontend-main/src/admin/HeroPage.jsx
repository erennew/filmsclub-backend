import React, { useCallback, useEffect, useMemo, useState } from "react";
import { FiArrowDown, FiArrowUp, FiPlus, FiRefreshCw, FiSearch, FiTrash2, FiX } from "react-icons/fi";
import { api } from "./api";
import {
  AdminErrorState,
  Badge,
  Button,
  EmptyState,
  Label,
  Modal,
  Panel,
  Select,
  Spinner,
  TextInput,
  cx,
  toast,
} from "./ui";

/* Curated hero carousel for the homepage. Empty picks = the backend serves its
   own newest-titles fallback, so this page is inert until someone pins a title. */

const keyOf = (p) => `${p.type}/${p.db_index}/${p.tmdb_id}`;
const trim = (picks) => picks.map(({ type, db_index, tmdb_id }) => ({ type, db_index, tmdb_id }));

export default function HeroPage() {
  const [picks, setPicks] = useState([]);
  const [max, setMax] = useState(12);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedJson, setSavedJson] = useState("[]");
  const [searchOpen, setSearchOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.get("/admin/api/hero");
      const rows = d.picks || [];
      setPicks(rows);
      setSavedJson(JSON.stringify(trim(rows)));
      setMax(d.max || 12);
      setError("");
    } catch (e) {
      setError(
        String(e.message).startsWith("404")
          ? "This backend has no hero endpoints yet — restart the API server (or deploy the backend) so /admin/api/hero exists. The homepage keeps serving its latest-releases carousel."
          : e.message
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = JSON.stringify(trim(picks)) !== savedJson;
  const broken = picks.filter((p) => p.resolved === false);
  const full = picks.length >= max;
  const used = useMemo(() => new Set(picks.map(keyOf)), [picks]);

  const move = (index, delta) =>
    setPicks((prev) => {
      const to = index + delta;
      if (to < 0 || to >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });

  const remove = (index) => setPicks((prev) => prev.filter((_, i) => i !== index));

  const add = (row) => {
    const kind = row.collection || row.type;
    if (kind !== "movie" && kind !== "tv") {
      toast("Only movies and series can be pinned", "error");
      return;
    }
    const pick = {
      type: kind,
      db_index: row.db_index,
      tmdb_id: row.tmdb_id,
      title: row.title,
      poster: row.poster,
      release_year: row.release_year,
      resolved: true,
    };
    if (used.has(keyOf(pick))) {
      toast("Already in the carousel", "error");
      return;
    }
    if (full) {
      toast(`The hero holds ${max} slots — remove one first`, "error");
      return;
    }
    setPicks((prev) => [...prev, pick]);
    setSearchOpen(false);
  };

  const save = async () => {
    if (broken.length) {
      toast(`${broken.length} slot(s) point at deleted titles — remove them first`, "error");
      return;
    }
    setBusy(true);
    try {
      await api.post("/admin/api/hero", { picks: trim(picks) });
      toast(picks.length ? "Hero carousel saved" : "Hero restored to automatic");
      await load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );

  if (error && picks.length === 0 && savedJson === "[]")
    return (
      <Panel>
        <AdminErrorState title="Hero settings unavailable" error={error} />
      </Panel>
    );

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Homepage</p>
          <h1 className="font-display text-3xl mt-1.5">Hero carousel</h1>
          <p className="text-sm text-bone/60 mt-1">
            {picks.length
              ? `${picks.length} of ${max} slots pinned, in slide order`
              : `Automatic — the newest ${max} titles until you pin something`}
          </p>
        </div>
        {error && <Badge tone="bad">{error}</Badge>}
        <Badge tone={picks.length ? "good" : "neutral"}>{picks.length ? "Curated" : "Auto"}</Badge>
        <Button variant="ghost" onClick={load} disabled={busy}>
          <FiRefreshCw size={14} /> Reload
        </Button>
        <Button variant="primary" disabled={busy || !dirty} onClick={save}>
          {dirty ? "Save changes" : "Saved"}
        </Button>
      </header>

      {broken.length > 0 && (
        <Panel
          title="Broken slots"
          subtitle="These titles are no longer in the library, so saving is blocked until they are removed"
        >
          <div className="flex flex-wrap gap-2">
            {picks.map(
              (p, i) =>
                p.resolved === false && (
                  <Button key={keyOf(p)} variant="danger" disabled={busy} onClick={() => remove(i)}>
                    <FiTrash2 size={14} /> Remove slot {i + 1}
                  </Button>
                )
            )}
          </div>
        </Panel>
      )}

      <Panel
        title="Slots"
        subtitle="The first slot is the slide the homepage opens on"
        actions={
          <>
            <Button variant="ghost" disabled={busy || picks.length === 0} onClick={() => setPicks([])}>
              Clear all
            </Button>
            <Button variant="primary" disabled={busy || full} onClick={() => setSearchOpen(true)}>
              <FiPlus size={14} /> Add title
            </Button>
          </>
        }
      >
        {picks.length === 0 ? (
          <EmptyState title="No pinned titles">
            The homepage falls back to the newest titles in the library. Pin a movie or series to take
            control.
          </EmptyState>
        ) : (
          <ul className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
            {picks.map((p, i) => (
              <li
                key={keyOf(p)}
                className={cx(
                  "relative rounded-md border overflow-hidden",
                  p.resolved === false ? "border-signal/50 bg-signal/6" : "border-bone/8 bg-ink/40"
                )}
              >
                <div className="relative aspect-[2/3] bg-carbon">
                  {p.poster ? (
                    <img src={p.poster} alt="" loading="lazy" className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-bone/50 timecode text-[10px] uppercase">
                      no poster
                    </div>
                  )}
                  <span className="absolute top-1.5 left-1.5 timecode text-[10px] px-1.5 py-0.5 rounded-[2px] bg-ink/80 border border-bone/20 text-projector">
                    {i + 1}
                  </span>
                  <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-ink via-ink/70 to-transparent pt-8 pb-2 px-2">
                    <p className="text-[11px] font-semibold text-bone leading-snug line-clamp-2">
                      {p.title || `${p.type} #${p.tmdb_id}`}
                    </p>
                    <p className="timecode text-[9px] text-bone/50 mt-1 uppercase tracking-wider">
                      {p.type === "tv" ? "Series" : "Movie"} • {p.release_year || "—"} • db {p.db_index}
                    </p>
                  </div>
                  {p.resolved === false && (
                    <span className="absolute top-1.5 right-1.5">
                      <Badge tone="bad">Missing</Badge>
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 px-2 py-1.5 border-t border-bone/6">
                  <Button variant="quiet" disabled={busy || i === 0} onClick={() => move(i, -1)}>
                    <FiArrowUp size={14} />
                  </Button>
                  <Button variant="quiet" disabled={busy || i === picks.length - 1} onClick={() => move(i, 1)}>
                    <FiArrowDown size={14} />
                  </Button>
                  <Button variant="quiet" className="ml-auto" disabled={busy} onClick={() => remove(i)}>
                    <FiX size={14} />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <TitlePicker
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onPick={add}
        used={used}
      />
    </div>
  );
}

function TitlePicker({ open, onClose, onPick, used }) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("movie");
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const d = await api.get("/admin/api/media", {
        media_type: type,
        query,
        sort: "year:desc",
        page: 1,
        page_size: 24,
      });
      setRows(d.items || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [type, query]);

  useEffect(() => {
    if (open) run();
  }, [open, run]);

  return (
    <Modal open={open} onClose={onClose} title="Add a title to the hero" width="max-w-3xl">
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div className="flex-1 min-w-[200px]">
          <Label htmlFor="hero-q">Search</Label>
          <div className="relative">
            <FiSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-bone/55" size={15} />
            <TextInput
              id="hero-q"
              value={query}
              placeholder="Title…"
              className="pl-9"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
          </div>
        </div>
        <div className="w-[160px]">
          <Label htmlFor="hero-type">Kind</Label>
          <Select id="hero-type" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="movie">Movies</option>
            <option value="tv">Series</option>
            <option value="all">Everything</option>
          </Select>
        </div>
        <Button variant="primary" disabled={busy} onClick={run}>
          <FiSearch size={14} /> Search
        </Button>
      </div>

      {error ? (
        <EmptyState title="Search failed">{error}</EmptyState>
      ) : busy ? (
        <div className="flex justify-center py-14">
          <Spinner className="w-6 h-6" />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState title="No titles found">Try a shorter search term.</EmptyState>
      ) : (
        <ul className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
          {rows.map((m) => {
            const k = `${m.collection || m.type}/${m.db_index}/${m.tmdb_id}`;
            const already = used.has(k);
            return (
              <li key={k} className="rounded-md border border-bone/8 bg-ink/40 p-2">
                <div className="aspect-[2/3] rounded-[4px] bg-carbon overflow-hidden">
                  {m.poster ? (
                    <img src={m.poster} alt="" loading="lazy" className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-bone/50 timecode text-[10px] uppercase">
                      no poster
                    </div>
                  )}
                </div>
                <p className="text-[11px] text-bone leading-snug line-clamp-2 mt-2">{m.title}</p>
                <p className="timecode text-[9px] text-bone/60 mt-1 uppercase tracking-wider">
                  {m.collection || m.type} • {m.release_year || "—"} • {m.file_count || 0}f
                </p>
                <Button
                  variant={already ? "ghost" : "primary"}
                  className="w-full mt-2"
                  disabled={already}
                  onClick={() => onPick(m)}
                >
                  {already ? "In carousel" : "Add"}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </Modal>
  );
}
