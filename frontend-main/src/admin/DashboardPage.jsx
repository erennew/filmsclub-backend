import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FiAlertTriangle, FiArrowRight, FiRefreshCw } from "react-icons/fi";
import { api } from "./api";
import { Badge, Button, EmptyState, Panel, Spinner, cx, toast } from "./ui";
import ViewsPanel from "./ViewsPanel";

function Stat({ label, value, hint, tone = "neutral" }) {
  const accents = {
    neutral: "text-bone",
    good: "text-projector",
    warn: "text-amber-200",
    bad: "text-signal",
  };
  return (
    <div className="glass-card rounded-lg px-4 py-4 relative overflow-hidden">
      <p className="timecode text-[9px] uppercase tracking-[0.22em] text-bone/60">{label}</p>
      <p className={cx("mt-2 font-display text-3xl leading-none tabular-nums", accents[tone])}>
        {value ?? "—"}
      </p>
      {hint && <p className="mt-2 text-[11px] text-bone/55 truncate">{hint}</p>}
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState(null);
  const [recent, setRecent] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    try {
      const [a, h, list] = await Promise.all([
        api.get("/admin/api/analytics"),
        api.get("/admin/api/health").catch(() => null),
        api
          .get("/admin/api/media", { media_type: "all", page: 1, page_size: 8, sort: "updated:desc" })
          .catch(() => ({ items: [] })),
      ]);
      setStats(a);
      setHealth(h);
      setRecent(list.items || []);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );

  const queue = health?.queue || {};
  const alerts = health?.alerts || [];
  const uptime = health?.system?.uptime_seconds;
  const uptimeLabel =
    uptime != null ? `${Math.floor(uptime / 3600)}h ${Math.floor((uptime % 3600) / 60)}m` : stats?.system?.uptime;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3 mb-1">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Overview</p>
          <h1 className="font-display text-3xl mt-1.5">Library at a glance</h1>
        </div>
        <Badge tone={health?.status === "ok" ? "ok" : "warn"}>
          {health?.status === "ok" ? "All systems nominal" : "Degraded"}
        </Badge>
        <Button variant="ghost" onClick={load} disabled={refreshing}>
          {refreshing ? <Spinner /> : <FiRefreshCw size={14} />} Refresh
        </Button>
      </header>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Stat label="Movies" value={stats?.media?.movies} tone="good" />
        <Stat label="Series" value={stats?.media?.tv_series} tone="good" />
        <Stat label="Anime" value={stats?.media?.anime} />
        <Stat label="Video files" value={stats?.files?.total} />
        <Stat label="Subtitles" value={stats?.files?.subtitles} />
        <Stat
          label="Queue pending"
          value={queue.pending ?? 0}
          tone={queue.pending > 500 ? "warn" : "neutral"}
          hint={queue.paused ? "queue paused" : `${queue.workers || 0} workers`}
        />
      </div>

      <div className="grid lg:grid-cols-[1.4fr_1fr] gap-5">
        <Panel title="Recently updated" subtitle="Last eight titles touched">
          {recent.length === 0 ? (
            <EmptyState title="Nothing here yet">Media saved by the bot will show up here.</EmptyState>
          ) : (
            <ul className="grid sm:grid-cols-2 gap-2.5">
              {recent.map((m) => (
                <li key={`${m.type}-${m.db_index}-${m.tmdb_id}`}>
                  <Link
                    to={`/admin/media/${m.collection || m.type}/${m.db_index}/${m.tmdb_id}`}
                    className="group flex items-center gap-3 rounded-md border border-bone/8 bg-ink/40 p-2.5 hover:border-projector/40 hover:bg-bone/4 transition-colors"
                  >
                    <img
                      src={m.poster || m.backdrop || ""}
                      alt=""
                      loading="lazy"
                      className="w-9 h-[3.25rem] object-cover rounded-[3px] bg-carbon ring-1 ring-inset ring-bone/10 shrink-0"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-bone truncate group-hover:text-projector transition-colors">
                        {m.title}
                      </span>
                      <span className="block timecode text-[10px] text-bone/60 mt-0.5">
                        {m.release_year || "—"} • {m.file_count || 0} files • {m.subtitle_count || 0} subs
                      </span>
                    </span>
                    <FiArrowRight className="text-bone/50 group-hover:text-projector shrink-0" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <div className="space-y-5">
          <ViewsPanel />
          <Panel title="Alerts" subtitle="Straight from the health check">
            {alerts.length === 0 ? (
              <p className="text-sm text-bone/50">No active alerts. 🎉</p>
            ) : (
              <ul className="space-y-2">
                {alerts.map((a, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-2.5 rounded-md border border-amber-300/30 bg-amber-300/6 px-3 py-2.5 text-sm text-amber-100/90"
                  >
                    <FiAlertTriangle className="mt-0.5 shrink-0 text-amber-300" size={14} />
                    <span>{a}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="System" subtitle="Backend vitals">
            <dl className="space-y-2.5 text-sm">
              {[
                ["Version", stats?.system?.version],
                ["Uptime", uptimeLabel],
                ["Python", health?.system?.python],
                ["ffmpeg", health?.system?.ffmpeg ? "available" : "missing"],
                ["Disk free", health?.system?.disk ? `${health.system.disk.free_gb} GB` : undefined],
              ].map(([k, v]) => (
                <div key={k} className="flex items-baseline gap-3">
                  <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 mr-auto">{k}</dt>
                  <dd className="timecode text-bone/80">{v ?? "—"}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-4 pt-4 border-t border-bone/6 flex flex-wrap gap-2">
              <Link to="/admin/queue">
                <Button variant="ghost">Queue desk</Button>
              </Link>
              <Link to="/admin/commands">
                <Button variant="ghost">Commands</Button>
              </Link>
              <Link to="/admin/health">
                <Button variant="ghost">Full health</Button>
              </Link>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
