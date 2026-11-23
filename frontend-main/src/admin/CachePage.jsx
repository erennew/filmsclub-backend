import React, { useEffect, useState } from "react";
import { FiRefreshCw, FiTrash2 } from "react-icons/fi";
import { api } from "./api";
import { AdminErrorState, Badge, Button, EmptyState, Panel, Spinner, formatEta, toast } from "./ui";

function Bar({ used, max }) {
  const pct = Math.min(100, max ? (used / max) * 100 : 0);
  return (
    <div className="h-1.5 rounded-full bg-bone/8 overflow-hidden">
      <div
        className={
          pct > 85
            ? "h-full rounded-full bg-signal transition-all"
            : "h-full rounded-full bg-projector/80 transition-all"
        }
        style={{ width: `${Math.max(2, pct)}%` }}
      />
    </div>
  );
}

export default function CachePage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      setData(await api.get("/admin/api/cache"));
      setError("");
    } catch (e) {
      setError(e.message);
      toast(e.message, "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clear = async (target) => {
    setBusy(target);
    try {
      await api.post("/admin/api/cache/clear", { target });
      toast(`${target === "all" ? "All" : target} cache cleared`);
      await load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy("");
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );
  if (!data)
    return (
      <Panel>
        <AdminErrorState
          title="Cache report unavailable"
          error={error || "Try refreshing — the backend may still be warming up."}
        />
      </Panel>
    );

  const caches = data.caches || {};
  const overall = caches.overall_stats || {};
  const remux = data.remux || {};
  const redis = data.redis || {};
  const chunks = caches.video_chunks || {};

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Performance</p>
          <h1 className="font-display text-3xl mt-1.5">Cache</h1>
          <p className="text-sm text-bone/60 mt-1">In-memory stores, remux files on disk and Redis health.</p>
        </div>
        <Button variant="ghost" onClick={load}>
          <FiRefreshCw size={14} /> Refresh
        </Button>
        <Button variant="danger" disabled={!!busy} onClick={() => clear("all")}>
          {busy === "all" ? <Spinner /> : <FiTrash2 size={14} />} Clear everything
        </Button>
      </header>

      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {[
          ["Redis", redis.status || (redis.available ? "connected" : "disabled"), redis.status === "error" ? "bad" : "ok"],
          ["File metadata", `${caches.file_metadata?.size ?? 0} / ${caches.file_metadata?.maxsize ?? "?"}`, "neutral"],
          ["Hot movies", `${caches.hot_movies?.size ?? 0} / ${caches.hot_movies?.maxsize ?? "?"}`, "neutral"],
          ["Hits / misses", `${overall.hits ?? 0} / ${overall.misses ?? 0}`, "neutral"],
        ].map(([label, value, tone]) => (
          <div key={label} className="glass-card rounded-lg px-4 py-4">
            <p className="timecode text-[9px] uppercase tracking-[0.2em] text-bone/60">{label}</p>
            <p className="mt-1.5 font-display text-2xl leading-none tabular-nums text-bone">{value}</p>
            <span className="inline-block mt-2">
              {tone !== "neutral" && <Badge tone={tone}>{tone === "ok" ? "healthy" : "check"}</Badge>}
            </span>
          </div>
        ))}
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        <Panel
          title="Remux directory"
          subtitle="ffmpeg output cached on disk"
          actions={
            <Button variant="ghost" disabled={!!busy} onClick={() => clear("remux")}>
              {busy === "remux" ? <Spinner /> : <FiTrash2 size={14} />} Clear
            </Button>
          }
        >
          <dl className="space-y-3 text-sm">
            <div>
              <div className="flex items-baseline gap-3 mb-1.5">
                <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 mr-auto">Budget used</dt>
                <dd className="timecode text-bone/80">
                  {remux.mb ?? 0} / {remux.budget_mb ?? "?"} MB
                </dd>
              </div>
              <Bar used={remux.mb} max={remux.budget_mb} />
            </div>
            {[
              ["Cached files", remux.files],
              ["Partial writes", remux.partials],
              ["Entry TTL", remux.ttl_hours ? formatEta(remux.ttl_hours * 3600) : "—"],
            ].map(([k, v]) => (
              <div key={k} className="flex items-baseline gap-3">
                <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 mr-auto">{k}</dt>
                <dd className="timecode text-bone/80">{v ?? "—"}</dd>
              </div>
            ))}
          </dl>
        </Panel>

        <Panel
          title="Video chunk cache"
          subtitle="Byte-range chunks served to the player"
          actions={
            <Button variant="ghost" disabled={!!busy} onClick={() => clear("video")}>
              {busy === "video" ? <Spinner /> : <FiTrash2 size={14} />} Clear
            </Button>
          }
        >
          <dl className="grid grid-cols-2 gap-y-3 gap-x-4 text-sm">
            {Object.entries(chunks).length === 0 ? (
              <div className="col-span-2">
                <EmptyState title="No chunk stats reported" />
              </div>
            ) : (
              Object.entries(chunks).map(([k, v]) => (
                <React.Fragment key={k}>
                  <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60">{k.replace(/_/g, " ")}</dt>
                  <dd className="timecode text-bone/80 text-right">
                    {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </dd>
                </React.Fragment>
              ))
            )}
          </dl>
        </Panel>
      </div>
    </div>
  );
}
