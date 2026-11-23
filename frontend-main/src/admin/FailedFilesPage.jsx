import React, { useEffect, useState } from "react";
import { FiRefreshCw, FiTrash2, FiX } from "react-icons/fi";
import { api } from "./api";
import { Badge, Button, EmptyState, Label, Panel, Spinner, TextInput, toast } from "./ui";

const PAGE_SIZE = 50;

export default function FailedFilesPage() {
  const [data, setData] = useState(null);
  const [page, setPage] = useState(1);
  const [reason, setReason] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async (targetPage = page) => {
    setLoading(true);
    try {
      const d = await api.get("/admin/api/failed-files", {
        page: targetPage,
        page_size: PAGE_SIZE,
        reason_filter: reason || undefined,
        date_from: from || undefined,
        date_to: to || undefined,
      });
      setData(d);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(1);
    setPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reason, from, to]);

  const guard = async (fn, ok) => {
    setBusy(true);
    try {
      await fn();
      toast(ok);
      await load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const items = data?.items || [];
  const total = data?.total_count || 0;
  const pages = data?.total_pages || 1;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Ingest errors</p>
          <h1 className="font-display text-3xl mt-1.5">Failed files</h1>
          <p className="text-sm text-bone/60 mt-1">
            {loading ? "Loading…" : `${total.toLocaleString()} entries waiting to be retried or cleared`}
          </p>
        </div>
        <Button
          variant="ghost"
          disabled={busy || total === 0}
          onClick={() =>
            guard(() => api.post("/admin/api/failed-files/retry-all", undefined, { reason_filter: reason || undefined, limit: 25 }), "Oldest 25 re-queued")
          }
        >
          <FiRefreshCw size={14} /> Retry oldest 25
        </Button>
        <Button
          variant="danger"
          disabled={busy || total === 0}
          onClick={() => {
            if (!window.confirm("Clear all failed-file log entries?")) return;
            guard(() => api.delete("/admin/api/failed-files", { reason_filter: reason || undefined }), "Log cleared");
          }}
        >
          <FiTrash2 size={14} /> Clear log
        </Button>
      </header>

      <Panel>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Label htmlFor="ff-text">Reason contains</Label>
            <TextInput
              id="ff-text"
              defaultValue={reason}
              placeholder="e.g. FloodWait"
              onKeyDown={(e) => e.key === "Enter" && setReason(e.currentTarget.value.trim())}
            />
          </div>
          <div>
            <Label htmlFor="ff-from">From</Label>
            <TextInput id="ff-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="ff-to">To</Label>
            <TextInput id="ff-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>
      </Panel>

      <Panel title="Entries" subtitle={`Page ${page} of ${pages}`}>
        {error ? (
          <EmptyState icon={<FiX />} title="Could not load failed files">
            {error}
          </EmptyState>
        ) : loading ? (
          <div className="flex justify-center py-14">
            <Spinner className="w-6 h-6" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState title="Nothing failed here">
            Every file the bot received made it into the library. 🎉
          </EmptyState>
        ) : (
          <ul className="space-y-2">
            {items.map((f) => (
              <li
                key={f._id || f.id}
                className="flex items-start gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-3 hover:border-signal/30 transition-colors"
              >
                <span className="w-8 h-8 rounded-md bg-signal/10 text-signal flex items-center justify-center shrink-0">
                  ⚠
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-bone truncate">{f.title || f.filename || "Untitled"}</p>
                  <p className="text-[12px] text-bone/50 mt-0.5 line-clamp-2">{f.reason || "Unknown reason"}</p>
                  <p className="timecode text-[10px] text-bone/55 mt-1">
                    {f.timestamp ? new Date(f.timestamp).toLocaleString() : "—"}
                    {f.filename ? ` • ${f.filename}` : ""}
                  </p>
                </div>
                <div className="flex gap-1.5 shrink-0">
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => guard(() => api.post(`/admin/api/failed-files/${f._id}/retry`), "Re-queued from Telegram")}
                  >
                    Retry
                  </Button>
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={() => guard(() => api.delete(`/admin/api/failed-files/${f._id}`), "Entry removed")}
                  >
                    <FiTrash2 size={14} />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {pages > 1 && (
          <nav className="mt-5 pt-4 border-t border-bone/6 flex items-center justify-center gap-2">
            <Button
              variant="ghost"
              disabled={page <= 1 || loading}
              onClick={() => {
                setPage(page - 1);
                load(page - 1);
              }}
            >
              Previous
            </Button>
            <Badge>{`${page} / ${pages}`}</Badge>
            <Button
              variant="ghost"
              disabled={page >= pages || loading}
              onClick={() => {
                setPage(page + 1);
                load(page + 1);
              }}
            >
              Next
            </Button>
          </nav>
        )}
      </Panel>
    </div>
  );
}
