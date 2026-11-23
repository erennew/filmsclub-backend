import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FiActivity, FiAlertTriangle, FiRefreshCw } from "react-icons/fi";
import { api } from "./api";
import { Badge, Button, EmptyState, Panel, Spinner, cx, formatEta, toast } from "./ui";

export default function HealthPage() {
  const [health, setHealth] = useState(null);
  const [lib, setLib] = useState(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);

  const load = async () => {
    try {
      const [h, l] = await Promise.all([
        api.get("/admin/api/health"),
        api.get("/admin/api/library-health", { sample_limit: 4 }).catch(() => null),
      ]);
      setHealth(h);
      setLib(l);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    if (!auto) return undefined;
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto]);

  if (loading)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );
  if (!health)
    return (
      <Panel>
        <EmptyState title="Health check failed">The backend did not answer — try again in a moment.</EmptyState>
      </Panel>
    );

  const sys = health.system || {};
  const q = health.queue || {};
  const clusters = health.clusters || [];
  const storage = health.storage?.clusters || [];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Diagnostics</p>
          <h1 className="font-display text-3xl mt-1.5 flex items-center gap-3">
            Health
            <Badge tone={health.status === "ok" ? "ok" : "warn"}>{health.status}</Badge>
          </h1>
        </div>
        <Button variant="ghost" onClick={() => setAuto((v) => !v)}>
          <FiActivity size={14} /> {auto ? "Auto on" : "Auto off"}
        </Button>
        <Button variant="ghost" onClick={load}>
          <FiRefreshCw size={14} /> Refresh
        </Button>
      </header>

      <Panel title="Alerts">
        {(health.alerts || []).length === 0 ? (
          <p className="text-sm text-bone/50">Nothing is on fire. 🎉</p>
        ) : (
          <ul className="space-y-2">
            {health.alerts.map((a, i) => (
              <li
                key={i}
                className="flex items-start gap-2.5 rounded-md border border-amber-300/30 bg-amber-300/6 px-3 py-2.5 text-sm text-amber-100/90"
              >
                <FiAlertTriangle className="mt-0.5 shrink-0 text-amber-300" size={14} />
                {a}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid lg:grid-cols-2 gap-5">
        <Panel title="MongoDB clusters" subtitle={`${clusters.length} reachable endpoints`}>
          <ul className="space-y-2">
            {clusters.map((c) => (
              <li key={c.cluster} className="flex items-center gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-2.5">
                <span
                  className={cx(
                    "w-2 h-2 rounded-full shrink-0",
                    c.status === "up" ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]" : "bg-signal"
                  )}
                />
                <span className="text-sm text-bone mr-auto">Cluster {c.cluster}</span>
                <span className="timecode text-[10px] text-bone/50 uppercase tracking-wider">
                  {c.error || c.status}
                </span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Storage quota" subtitle="Per-cluster usage against the configured cap">
          {storage.length === 0 ? (
            <p className="text-sm text-bone/60">Storage report unavailable.</p>
          ) : (
            <ul className="space-y-3.5">
              {storage.map((c) => (
                <li key={c.cluster}>
                  <div className="flex items-baseline gap-3 mb-1.5">
                    <span className="text-sm text-bone mr-auto">Cluster {c.cluster}</span>
                    <span className="timecode text-[11px] text-bone/60">
                      {c.used_mb ?? 0} / {c.quota_mb ?? "?"} MB
                    </span>
                    <Badge tone={c.status === "alert" ? "warn" : "ok"}>{c.used_percent ?? 0}%</Badge>
                  </div>
                  <div className="h-1.5 rounded-full bg-bone/8 overflow-hidden">
                    <div
                      className={cx(
                        "h-full rounded-full transition-all",
                        c.status === "alert" ? "bg-signal" : "bg-projector/80"
                      )}
                      style={{ width: `${Math.max(2, Math.min(100, c.used_percent || 0))}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Runtime" subtitle="Backend process and tooling">
          <dl className="grid grid-cols-2 gap-y-3 gap-x-4 text-sm">
            {[
              ["Version", `v${sys.version || "—"}`],
              ["Python", sys.python],
              ["Uptime", sys.uptime_seconds ? formatEta(sys.uptime_seconds) : "—"],
              ["ffmpeg", sys.ffmpeg ? "available" : "missing"],
              ["ffprobe", sys.ffprobe ? "available" : "missing"],
              ["Disk free", sys.disk ? `${sys.disk.free_gb} / ${sys.disk.total_gb} GB` : "—"],
            ].map(([k, v]) => (
              <React.Fragment key={k}>
                <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60">{k}</dt>
                <dd className="timecode text-bone/80 text-right">{v ?? "—"}</dd>
              </React.Fragment>
            ))}
          </dl>
        </Panel>

        <Panel title="Queue" subtitle="Live ingest pressure">
          <dl className="grid grid-cols-2 gap-y-3 gap-x-4 text-sm">
            {[
              ["State", q.paused ? "paused" : "running"],
              ["Engine", q.engine],
              ["Pending", q.pending],
              ["Processing", q.processing],
              ["Failed", q.failed],
              ["FloodWait", q.floodwait_seconds ? `${q.floodwait_seconds}s` : "none"],
              ["ETA", q.eta_seconds ? formatEta(q.eta_seconds) : "—"],
            ].map(([k, v]) => (
              <React.Fragment key={k}>
                <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60">{k}</dt>
                <dd className="timecode text-bone/80 text-right">{v ?? "—"}</dd>
              </React.Fragment>
            ))}
          </dl>
          <Link
            to="/admin/queue"
            className="mt-4 inline-flex items-center gap-2 text-sm text-projector/80 hover:text-projector transition-colors"
          >
            Open the queue desk →
          </Link>
        </Panel>
      </div>

      {lib && (
        <Panel
          title="Library integrity"
          subtitle={`${lib.totals?.documents ?? 0} titles • ${lib.totals?.files ?? 0} files • ${
            lib.totals?.subtitles ?? 0
          } subs • ${lib.totals?.episodes ?? 0} episodes`}
        >
          <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {(lib.categories || []).map((c) => (
              <div
                key={c.key}
                className={cx(
                  "rounded-md border px-3.5 py-3",
                  c.count ? "border-amber-300/25 bg-amber-300/5" : "border-bone/8 bg-ink/40"
                )}
              >
                <div className="flex items-baseline gap-2">
                  <p className="text-sm text-bone mr-auto">{c.label}</p>
                  <p
                    className={cx(
                      "font-display text-xl leading-none tabular-nums",
                      c.count ? "text-amber-200" : "text-emerald-300/80"
                    )}
                  >
                    {c.count}
                  </p>
                </div>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {(c.items || []).slice(0, 3).map((i) => (
                    <Link
                      key={`${i.type}-${i.tmdb_id}`}
                      to={`/admin/media/${i.type}/${i.db_index || 1}/${i.tmdb_id}`}
                      className="text-[11px] px-2 py-0.5 rounded-[3px] border border-bone/12 text-bone/60 hover:text-projector hover:border-projector/40 transition-colors truncate max-w-[150px]"
                    >
                      {i.title}
                    </Link>
                  ))}
                  {(c.items || []).length === 0 && (
                    <span className="text-[11px] text-bone/55">Nothing to fix</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
