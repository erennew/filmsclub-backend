import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { FiCheck, FiRefreshCw, FiSearch, FiTrash2, FiX } from "react-icons/fi";
import { api } from "./api";
import {
  Badge,
  Button,
  ConfirmModal,
  EmptyState,
  Label,
  Panel,
  Select,
  TextInput,
  cx,
  toast,
} from "./ui";

const TYPES = [
  { id: "movie", label: "Movies" },
  { id: "tv", label: "Series" },
  { id: "anime", label: "Anime" },
  { id: "all", label: "Everything" },
];

const FLAGS = [
  { id: "", label: "No quality filter" },
  { id: "no_files", label: "Missing video files" },
  { id: "no_subs", label: "Files but no subtitles" },
  { id: "has_subs", label: "Has subtitles" },
  { id: "no_poster", label: "Missing poster" },
  { id: "no_backdrop", label: "Missing backdrop" },
  { id: "no_description", label: "Missing description" },
  { id: "no_genres", label: "Missing genres" },
  { id: "no_cast", label: "Missing cast" },
  { id: "no_episodes", label: "Series with no episodes" },
];

const SORTS = [
  { id: "updated:desc", label: "Recently updated" },
  { id: "year:desc", label: "Newest release year" },
  { id: "rating:desc", label: "Highest rated" },
  { id: "title:asc", label: "Title A–Z" },
  { id: "files:desc", label: "Most files" },
];

const PAGE_SIZE = 24;

export default function MediaLibraryPage() {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [confirm, setConfirm] = useState(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [nonce, setNonce] = useState(0);
  const page = Math.max(1, Number(params.get("page") || 1));
  const type = params.get("type") || "movie";
  const searchInput = useRef(null);

  const filters = useMemo(
    () => ({
      media_type: type,
      query: params.get("q") || "",
      language: params.get("language") || "",
      genre: params.get("genre") || "",
      flag: params.get("flag") || "",
      sort: params.get("sort") || "updated:desc",
      page,
      page_size: PAGE_SIZE,
    }),
    [params, type, page]
  );

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    api
      .get("/admin/api/media", filters)
      .then((d) => {
        if (!alive) return;
        setRows(d.items || []);
        setTotal(d.total_count || 0);
        setPages(Math.max(1, Math.ceil((d.total_count || 0) / PAGE_SIZE)));
      })
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [filters, nonce]);

  useEffect(() => setSelected(new Set()), [filters]);

  const setParam = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, String(value));
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setParams(next, { replace: true });
  };

  const key = (m) => `${m.type}/${m.db_index}/${m.tmdb_id}`;
  const toggle = (m) =>
    setSelected((prev) => {
      const next = new Set(prev);
      const k = key(m);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const selectedRows = rows.filter((m) => selected.has(key(m)));

  const runBulk = async (action, extra = {}) => {
    setBulkBusy(true);
    try {
      const items = selectedRows.map((m) => ({
        type: m.collection || m.type,
        db_index: m.db_index,
        tmdb_id: m.tmdb_id,
      }));
      const res = await api.post("/admin/api/media/bulk", { action, items, ...extra });
      toast(`${res.succeeded}/${res.attempted} done${res.failed ? ` • ${res.failed} failed` : ""}`);
      if (res.errors?.length) toast(res.errors[0], "info");
      setConfirm(null);
      setSelected(new Set());
      setNonce((n) => n + 1);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Catalogue</p>
          <h1 className="font-display text-3xl mt-1.5">Media library</h1>
          <p className="text-sm text-bone/60 mt-1">
            {loading ? "Scanning clusters…" : `${total.toLocaleString()} titles match your filters`}
          </p>
        </div>
        <div className="flex gap-1 p-1 rounded-md bg-bone/4 border border-bone/8">
          {TYPES.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setParam("type", t.id === "movie" ? "" : t.id)}
              className={cx(
                "px-3 py-1.5 rounded-[4px] text-sm transition-colors",
                type === t.id ? "bg-projector text-ink font-semibold" : "text-bone/55 hover:text-bone"
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      </header>

      <Panel>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="lg:col-span-2">
            <Label htmlFor="lib-q">Search</Label>
            <div className="relative">
              <FiSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-bone/55" size={15} />
              <TextInput
                id="lib-q"
                ref={searchInput}
                defaultValue={params.get("q") || ""}
                placeholder="Title or file name…"
                className="pl-9"
                onKeyDown={(e) => e.key === "Enter" && setParam("q", e.currentTarget.value.trim())}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="lib-flag">Quality filter</Label>
            <Select id="lib-flag" value={params.get("flag") || ""} onChange={(e) => setParam("flag", e.target.value)}>
              {FLAGS.map((f) => (
                <option key={f.id || "none"} value={f.id}>
                  {f.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="lib-sort">Sort by</Label>
            <Select
              id="lib-sort"
              value={params.get("sort") || "updated:desc"}
              onChange={(e) => setParam("sort", e.target.value)}
            >
              {SORTS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="lib-lang">Language</Label>
            <TextInput
              id="lib-lang"
              defaultValue={params.get("language") || ""}
              placeholder="tamil"
              onKeyDown={(e) => e.key === "Enter" && setParam("language", e.currentTarget.value.trim())}
            />
          </div>
          <div>
            <Label htmlFor="lib-genre">Genre</Label>
            <TextInput
              id="lib-genre"
              defaultValue={params.get("genre") || ""}
              placeholder="action"
              onKeyDown={(e) => e.key === "Enter" && setParam("genre", e.currentTarget.value.trim())}
            />
          </div>
          <div className="flex items-end gap-2 sm:col-span-2">
            <Button variant="primary" onClick={() => setParam("q", searchInput.current?.value.trim() || "")}>
              <FiSearch size={14} /> Apply
            </Button>
            {params.size > 0 && (
              <Button variant="quiet" onClick={() => setParams({}, { replace: true })}>
                <FiX size={14} /> Clear filters
              </Button>
            )}
          </div>
        </div>
      </Panel>

      {selected.size > 0 && (
        <div className="sticky top-0 z-30 glass-dark rounded-lg px-4 py-3 flex flex-wrap items-center gap-2 shadow-xl">
          <Badge tone="good">{selected.size} selected</Badge>
          <span className="mr-auto timecode text-[10px] uppercase tracking-[0.16em] text-bone/60">
            Bulk actions apply to the selected titles on this page
          </span>
          <Button variant="ghost" disabled={bulkBusy} onClick={() => runBulk("refresh")}>
            <FiRefreshCw size={14} /> Refresh TMDB
          </Button>
          <Button variant="danger" disabled={bulkBusy} onClick={() => setConfirm("telegram")}>
            <FiTrash2 size={14} /> Telegram + DB
          </Button>
          <Button variant="danger" disabled={bulkBusy} onClick={() => setConfirm("delete")}>
            <FiTrash2 size={14} /> DB only
          </Button>
        </div>
      )}

      <Panel>
        {error ? (
          <EmptyState icon={<FiX />} title="Could not load media">
            {error}
          </EmptyState>
        ) : loading ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="animate-pulse">
                <div className="aspect-[2/3] rounded-[4px] bg-bone/6" />
                <div className="h-3 rounded bg-bone/6 mt-2.5" />
              </div>
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState title="No titles found">Loosen the filters or try a different search.</EmptyState>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
            {rows.map((m) => {
              const isSel = selected.has(key(m));
              return (
                <div
                  key={key(m)}
                  className={cx(
                    "group relative rounded-md border transition-colors overflow-hidden",
                    isSel ? "border-projector/70 bg-projector/6" : "border-bone/8 bg-ink/40 hover:border-bone/20"
                  )}
                >
                  <Link
                    to={`/admin/media/${m.collection || m.type}/${m.db_index}/${m.tmdb_id}`}
                    className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-projector/70"
                  >
                    <div className="relative aspect-[2/3] bg-carbon">
                      {m.poster ? (
                        <img
                          src={m.poster}
                          alt=""
                          loading="lazy"
                          className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-bone/50 text-[10px] timecode uppercase">
                          no poster
                        </div>
                      )}
                      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-ink via-ink/70 to-transparent pt-8 pb-2 px-2">
                        <p className="text-[11px] font-semibold text-bone leading-snug line-clamp-2">{m.title}</p>
                        <p className="timecode text-[9px] text-bone/50 mt-1 uppercase tracking-wider">
                          {m.release_year || "—"} • {m.file_count || 0}f • {m.subtitle_count || 0}s
                        </p>
                      </div>
                      {m.file_count === 0 && (
                        <span className="absolute top-1.5 left-1.5">
                          <Badge tone="bad">0 files</Badge>
                        </span>
                      )}
                    </div>
                  </Link>
                  <button
                    type="button"
                    aria-label={isSel ? "Deselect" : "Select"}
                    onClick={() => toggle(m)}
                    className={cx(
                      "absolute top-1.5 right-1.5 w-6 h-6 rounded-[4px] border flex items-center justify-center transition-all",
                      isSel
                        ? "bg-projector border-projector text-ink"
                        : "border-bone/25 bg-ink/70 text-transparent opacity-0 group-hover:opacity-100 hover:border-bone/60"
                    )}
                  >
                    <FiCheck size={13} />
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {pages > 1 && (
          <nav className="mt-5 pt-4 border-t border-bone/6 flex items-center justify-center gap-2">
            <Button variant="ghost" disabled={page <= 1 || loading} onClick={() => setParam("page", page - 1)}>
              Previous
            </Button>
            <span className="timecode text-[11px] text-bone/50 px-2">
              page {page} / {pages}
            </span>
            <Button variant="ghost" disabled={page >= pages || loading} onClick={() => setParam("page", page + 1)}>
              Next
            </Button>
          </nav>
        )}
      </Panel>

      <ConfirmModal
        open={confirm === "delete"}
        onClose={() => setConfirm(null)}
        busy={bulkBusy}
        title={`Delete ${selected.size} titles from the database`}
        confirmWord="DELETE"
        confirmLabel="Delete from DB"
        onConfirm={() => runBulk("delete")}
      />
      <ConfirmModal
        open={confirm === "telegram"}
        onClose={() => setConfirm(null)}
        busy={bulkBusy}
        title="Delete from Telegram and the database"
        confirmWord="DELETE"
        confirmLabel="Delete everything"
        onConfirm={() => runBulk("delete_telegram")}
      />
    </div>
  );
}
