import React, { useEffect, useState } from "react";
import { FiActivity, FiCheck, FiPause, FiPlay, FiRefreshCw, FiTrash2, FiUnlock } from "react-icons/fi";
import { api } from "./api";
import { AdminErrorState, Badge, Button, ConfirmModal, Label, Panel, Spinner, TextArea, cx, formatEta, toast } from "./ui";

function Metric({ label, value, tone }) {
  return (
    <div className="rounded-lg border border-bone/8 bg-ink/40 px-4 py-3.5">
      <p className="timecode text-[9px] uppercase tracking-[0.2em] text-bone/60">{label}</p>
      <p
        className={cx(
          "mt-1.5 font-display text-2xl leading-none tabular-nums",
          tone === "warn" ? "text-amber-200" : tone === "bad" ? "text-signal" : "text-bone"
        )}
      >
        {value ?? "—"}
      </p>
    </div>
  );
}

export default function QueuePage() {
  const [snap, setSnap] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [links, setLinks] = useState("");
  const [preview, setPreview] = useState(null);
  const [auto, setAuto] = useState(true);

  const load = async () => {
    try {
      const d = await api.get("/admin/api/queue");
      setSnap(d);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
    if (!auto) return undefined;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [auto]);

  const act = async (label, fn) => {
    setBusy(true);
    try {
      await fn();
      toast(label);
      await load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  if (error && !snap)
    return (
      <Panel>
        <AdminErrorState title="Queue unavailable" error={error} />
      </Panel>
    );
  if (!snap)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );

  const q = snap;
  const workerReport = q.worker_report || null;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Ingest</p>
          <h1 className="font-display text-3xl mt-1.5">Queue desk</h1>
          <p className="text-sm text-bone/60 mt-1">
            {q.engine} engine • {q.workers} worker{q.workers === 1 ? "" : "s"}
            {workerReport?.supervised
              ? ` • live: ${(workerReport.running || []).length} (asked for ${workerReport.configured})`
              : ""}
            {auto ? " • refreshing every 5s" : ""}
          </p>
        </div>
        <Badge tone={q.paused ? "warn" : "ok"}>{q.paused ? "Paused" : "Running"}</Badge>
        <Button variant="ghost" onClick={() => setAuto((v) => !v)}>
          <FiActivity size={14} /> {auto ? "Auto-refresh on" : "Auto-refresh off"}
        </Button>
        <Button variant="ghost" onClick={load} disabled={busy}>
          <FiRefreshCw size={14} />
        </Button>
      </header>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Metric label="Pending" value={q.pending} tone={q.pending > 500 ? "warn" : undefined} />
        <Metric label="Processing" value={q.processing} />
        <Metric label="Failed" value={q.failed} tone={q.failed ? "bad" : undefined} />
        <Metric label="Retries" value={q.retries} />
        <Metric label="FloodWait" value={q.floodwait_seconds ? `${q.floodwait_seconds}s` : "none"} tone={q.floodwait_seconds ? "warn" : undefined} />
        <Metric label="ETA" value={formatEta(q.eta_seconds)} />
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        <Panel
          title="In progress"
          subtitle="What each worker holds right now"
          actions={
            q.paused ? (
              <Button variant="primary" disabled={busy} onClick={() => act("Queue resumed", () => api.post("/admin/api/queue/resume"))}>
                <FiPlay size={14} /> Resume
              </Button>
            ) : (
              <Button variant="ghost" disabled={busy} onClick={() => act("Queue paused", () => api.post("/admin/api/queue/pause"))}>
                <FiPause size={14} /> Pause
              </Button>
            )
          }
        >
          {(q.processing_items || []).length === 0 ? (
            <p className="text-sm text-bone/60">No worker is holding a file at this moment.</p>
          ) : (
            <ul className="space-y-2">
              {(q.processing_items || []).map((p, i) => (
                <li key={i} className="flex items-center gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-2.5">
                  <span className="w-8 h-8 rounded-md bg-projector/10 flex items-center justify-center text-projector shrink-0">
                    🎬
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-bone truncate">{p.title || "Untitled"}</p>
                    <p className="timecode text-[10px] text-bone/60 mt-0.5">
                      {p.channel_id || "—"}/{p.message_id || "—"} •{" "}
                      {p.claimed_seconds != null ? `${formatEta(p.claimed_seconds)} in progress` : "claimed"}
                      {p.retry_count ? ` • retry ${p.retry_count}` : ""}
                    </p>
                  </div>
                  <Badge>{p.claimed_by || "worker"}</Badge>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-4 pt-4 border-t border-bone/6 flex items-center gap-2">
            <Button variant="danger" disabled={busy} onClick={() => setConfirmClear(true)}>
              <FiTrash2 size={14} /> Clear queue
            </Button>
            <span className="text-[11px] text-bone/55">Removes every queued item, processed record and checkpoint.</span>
          </div>
        </Panel>

        <div className="space-y-5">
          <Panel title="Per-channel backlog" subtitle="Where the pending files are waiting">
            {(q.channels || []).length === 0 ? (
              <p className="text-sm text-bone/60">No per-channel backlog recorded.</p>
            ) : (
              <ul className="space-y-1.5">
                {q.channels.map((c, i) => {
                  const max = Math.max(...q.channels.map((x) => x.pending || 0), 1);
                  return (
                    <li key={i} className="flex items-center gap-3">
                      <span className="timecode text-[11px] text-bone/55 w-28 shrink-0 truncate">{c.channel_id}</span>
                      <span className="flex-1 h-1.5 rounded-full bg-bone/8 overflow-hidden">
                        <span
                          className="block h-full rounded-full bg-projector/80"
                          style={{ width: `${Math.max(2, ((c.pending || 0) / max) * 100)}%` }}
                        />
                      </span>
                      <span className="timecode text-[11px] text-bone/70 w-16 text-right">{c.pending}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </Panel>

          <Panel title="Queue settings" subtitle="Live values from the backend">
            <dl className="grid grid-cols-2 gap-y-2.5 gap-x-4 text-sm">
              {[
                ["File delay", `${q.settings?.file_delay ?? "—"}s`],
                ["Batch size", q.settings?.batch_size],
                ["Batch gap", `${q.settings?.batch_gap ?? "—"}s`],
                ["Max retry", q.settings?.max_retry],
                ["Queue TTL", `${q.settings?.queue_ttl_days ?? "—"} days`],
                ["Counters", Object.entries(q.counters || {}).map(([k, v]) => `${k} ${v}`).join(" • ")],
              ].map(([k, v]) => (
                <React.Fragment key={k}>
                  <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60">{k}</dt>
                  <dd className="timecode text-bone/80 text-right truncate">{v ?? "—"}</dd>
                </React.Fragment>
              ))}
            </dl>
            {workerReport ? (
              <div className="mt-4 pt-4 border-t border-bone/6">
                <p className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 mb-2">
                  Workers
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="good">
                    running {(workerReport.running || []).join(", ") || "none"}
                  </Badge>
                  {(workerReport.retiring || []).length ? (
                    <Badge tone="warn">retiring {workerReport.retiring.join(", ")}</Badge>
                  ) : null}
                  <span className="timecode text-[10px] text-bone/55">
                    asked for {workerReport.configured}
                    {!workerReport.supervised ? " · restart-only in this engine" : ""}
                  </span>
                </div>
              </div>
            ) : null}
          </Panel>
        </div>
      </div>

      <Panel
        title="Stuck claims"
        subtitle="Items a worker holds right now"
        actions={
          <Button
            variant="danger"
            disabled={busy || (q.claims || []).length === 0}
            onClick={() =>
              act("Claims released", () =>
                api.post("/admin/api/queue/claims/release", { older_than_seconds: 0 })
              )
            }
          >
            <FiUnlock size={14} /> Release all
          </Button>
        }
      >
        {(q.claims || []).length === 0 ? (
          <p className="text-sm text-bone/60">
            Nothing is claimed right now. A claim older than the backend timeout is
            returned to the queue automatically — this is the manual shortcut.
          </p>
        ) : (
          <ul className="space-y-2">
            {(q.claims || []).map((c) => (
              <li
                key={c.id}
                className="flex flex-wrap items-center gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-bone truncate">
                    {c.title || "Untitled"}
                  </p>
                  <p className="timecode text-[10px] text-bone/60 mt-0.5">
                    {c.channel ?? "—"}/{c.msg_id ?? "—"} •{" "}
                    {c.age_seconds != null ? `held for ${formatEta(c.age_seconds)}` : "claim age unknown"}
                    {c.claimed_by ? ` • ${c.claimed_by}` : ""}
                    {c.claim_releases ? ` • released ${c.claim_releases}× before` : ""}
                  </p>
                </div>
                <Badge tone={(c.age_seconds || 0) > 3600 ? "warn" : "neutral"}>
                  {c.claimed_by || "worker"}
                </Badge>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() =>
                    act("Claim released", () =>
                      api.post("/admin/api/queue/claims/release", { ids: [c.id] })
                    )
                  }
                >
                  <FiUnlock size={13} /> Release
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Bulk add from Telegram links"
        subtitle="Paste t.me post links, one per line"
        actions={
          <>
            <Button
              variant="ghost"
              disabled={busy || !links.trim()}
              onClick={() =>
                act("Preview ready", async () => {
                  setPreview(await api.post("/admin/api/queue/links", { links, preview: true }));
                })
              }
            >
              Preview
            </Button>
            <Button
              variant="primary"
              disabled={busy || !links.trim()}
              onClick={() =>
                act("Links queued", async () => {
                  setPreview(await api.post("/admin/api/queue/links", { links, preview: false }));
                  setLinks("");
                })
              }
            >
              <FiCheck size={14} /> Queue them
            </Button>
          </>
        }
      >
        <Label htmlFor="queue-links">Links</Label>
        <TextArea
          id="queue-links"
          value={links}
          onChange={(e) => setLinks(e.target.value)}
          rows={5}
          className="font-mono text-[12px]"
          placeholder={"https://t.me/c/123456/789\nhttps://t.me/mychannel/42"}
        />
        {preview && (
          <pre className="mt-3 timecode text-[11px] leading-relaxed text-bone/60 bg-ink/60 border border-bone/8 rounded-md p-3 max-h-56 overflow-auto whitespace-pre-wrap break-all">
            {JSON.stringify(preview, null, 2)}
          </pre>
        )}
      </Panel>

      <ConfirmModal
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        busy={busy}
        title="Clear the entire queue"
        confirmWord="CLEAR"
        confirmLabel="Clear everything"
        onConfirm={() => act("Queue cleared", () => api.post("/admin/api/queue/clear", { confirm: "CLEAR" }))
          .finally(() => setConfirmClear(false))
        }
      />
    </div>
  );
}
