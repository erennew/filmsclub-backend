import React, { useCallback, useEffect, useState } from "react";
import { FiChevronLeft, FiChevronRight, FiRefreshCw } from "react-icons/fi";
import { api } from "./api";
import { AdminErrorState, Badge, Button, EmptyState, Label, Panel, Spinner, TextInput, cx, toast } from "./ui";

function resultTone(result) {
  if (!result) return "neutral";
  if (result === "ok") return "ok";
  return "bad";
}

function toIso(value) {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString();
}

/* Newest-first view of the admin audit log. Retention is TTL-only — there is
   no delete endpoint. */
export default function AuditPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const d = await api.get("/admin/api/audit", {
        actor,
        action,
        date_from: toIso(dateFrom),
        date_to: toIso(dateTo),
        page,
        page_size: pageSize,
      });
      setData(d);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [actor, action, dateFrom, dateTo, page, pageSize]);

  useEffect(() => {
    load();
  }, [load]);

  const applyFilters = () => {
    // ``load`` closes over the current page. Going back to page 1 must wait
    // for the state change, otherwise the request still uses the old page.
    if (page === 1) load();
    else setPage(1);
  };

  const clearFilters = () => {
    setActor("");
    setAction("");
    setDateFrom("");
    setDateTo("");
    setPage(1);
  };

  if (error && !data)
    return (
      <Panel>
        <AdminErrorState title="Audit log unavailable" error={error} />
      </Panel>
    );
  if (!data)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );

  const items = data.items || [];
  const total = data.total_count || 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Oversight</p>
          <h1 className="font-display text-3xl mt-1.5">Audit log</h1>
          <p className="text-sm text-bone/60 mt-1">
            {total.toLocaleString()} entries · newest first · retained 90 days
          </p>
        </div>
        <Button variant="ghost" onClick={load} disabled={busy}>
          <FiRefreshCw size={14} /> Refresh
        </Button>
      </header>

      <Panel title="Filters" subtitle="Actor/action match is a case-insensitive substring; dates are inclusive">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_1fr_auto_auto] items-end">
          <div>
            <Label htmlFor="audit-actor">Actor</Label>
            <TextInput
              id="audit-actor"
              value={actor}
              placeholder="admin"
              onChange={(e) => setActor(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            />
          </div>
          <div>
            <Label htmlFor="audit-action">Action</Label>
            <TextInput
              id="audit-action"
              value={action}
              placeholder="media.delete"
              onChange={(e) => setAction(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            />
          </div>
          <div>
            <Label htmlFor="audit-from">From</Label>
            <TextInput
              id="audit-from"
              type="datetime-local"
              value={dateFrom}
              className="[color-scheme:dark]"
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="audit-to">To</Label>
            <TextInput
              id="audit-to"
              type="datetime-local"
              value={dateTo}
              className="[color-scheme:dark]"
              onChange={(e) => setDateTo(e.target.value)}
            />
          </div>
          <Button variant="primary" onClick={applyFilters} disabled={busy}>
            Apply
          </Button>
          <Button variant="ghost" onClick={clearFilters} disabled={busy}>
            Clear
          </Button>
        </div>
      </Panel>

      <Panel title="Entries" subtitle="Every admin mutation, from the panel and from Telegram">
        {items.length === 0 ? (
          <EmptyState title="No audit entries match">
            Mutations appear here as soon as they happen.
          </EmptyState>
        ) : (
          <ul className="space-y-2">
            {items.map((row) => (
              <li
                key={row.id}
                className="rounded-md border border-bone/8 bg-ink/40 px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-bone">{row.action}</span>
                  <Badge tone={row.actor_kind === "telegram" ? "warn" : "neutral"}>
                    {row.actor_kind}
                  </Badge>
                  <Badge tone={resultTone(row.result)}>
                    {row.result === "ok" ? "ok" : "error"}
                  </Badge>
                  <span className="timecode text-[10px] text-bone/50 ml-auto tabular-nums">
                    {row.ts ? new Date(row.ts).toLocaleString() : "—"}
                  </span>
                </div>
                <p className="timecode text-[11px] text-bone/50 mt-1">
                  {row.actor} → {row.target || "—"}
                  {row.ip ? ` · ${row.ip}` : ""}
                </p>
                {row.detail ? (
                  <p className="text-[12px] text-bone/55 mt-1.5 break-words whitespace-pre-wrap">
                    {row.detail}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        <div className="mt-4 pt-4 border-t border-bone/6 flex items-center justify-between">
          <p className="timecode text-[10px] text-bone/60">
            page {data.page} of {pages}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              disabled={busy || data.page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <FiChevronLeft size={14} /> Newer
            </Button>
            <Button
              variant="ghost"
              disabled={busy || data.page >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Older <FiChevronRight size={14} />
            </Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}
