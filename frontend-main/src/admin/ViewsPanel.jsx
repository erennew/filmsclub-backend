import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "./api";
import { Badge, EmptyState, Panel, Spinner, cx, toast } from "./ui";

const RANGES = [7, 14, 30];

export default function ViewsPanel() {
  const [days, setDays] = useState(7);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .get("/admin/api/views-analytics", { days, limit: 8 })
      .then((d) => alive && setData(d))
      .catch((e) => alive && toast(e.message, "error"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [days]);

  const daily = data?.daily || [];
  const peak = Math.max(1, ...daily.map((d) => d.count || 0));
  const totals = data?.totals || {};

  return (
    <Panel
      title="Views"
      subtitle={`${(totals.today ?? 0).toLocaleString()} today • ${(totals.all_time ?? 0).toLocaleString()} all time`}
      actions={
        <div className="flex gap-1 p-1 rounded-md bg-bone/4 border border-bone/8">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setDays(r)}
              className={cx(
                "px-2.5 py-1 rounded-[4px] timecode text-[10px] uppercase tracking-wider transition-colors",
                days === r ? "bg-projector text-ink font-semibold" : "text-bone/50 hover:text-bone"
              )}
            >
              {r}d
            </button>
          ))}
        </div>
      }
    >
      {loading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : daily.length === 0 ? (
        <EmptyState title="No view data yet">Play counts appear once viewers start watching.</EmptyState>
      ) : (
        <>
          <div className="flex items-end gap-1 h-24 mb-1">
            {daily.map((d) => (
              <div
                key={d.date}
                title={`${d.date}: ${d.count} views`}
                className="flex-1 rounded-t-[2px] bg-gradient-to-t from-projector/40 to-projector/85 hover:from-projector/60 hover:to-projector transition-all"
                style={{ height: `${Math.max(4, (d.count / peak) * 100)}%` }}
              />
            ))}
          </div>
          <div className="flex justify-between timecode text-[9px] text-bone/55 mb-4">
            <span>{daily[0]?.date?.slice(5)}</span>
            <span>{daily[daily.length - 1]?.date?.slice(5)}</span>
          </div>
          <p className="timecode text-[10px] uppercase tracking-[0.16em] text-bone/60 mb-2">Most watched</p>
          <ol className="space-y-1.5">
            {(data.top || []).map((t, i) => (
              <li key={`${t.media_type}-${t.tmdb_id}`}>
                <Link
                  to={`/admin/media/${t.media_type}/1/${t.tmdb_id}`}
                  className="group flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-bone/5 transition-colors"
                >
                  <span className="timecode text-[11px] text-projector/70 w-4 shrink-0">{i + 1}</span>
                  <span className="text-sm text-bone/80 truncate group-hover:text-projector transition-colors mr-auto">
                    {t.title}
                  </span>
                  <Badge tone="neutral">{(t.views || 0).toLocaleString()}</Badge>
                </Link>
              </li>
            ))}
          </ol>
        </>
      )}
    </Panel>
  );
}
