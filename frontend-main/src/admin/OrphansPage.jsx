import React, { useCallback, useEffect, useState } from "react";
import { FiChevronLeft, FiChevronRight, FiRefreshCw, FiUpload } from "react-icons/fi";
import { api } from "./api";
import { AdminErrorState, Badge, Button, EmptyState, Label, Panel, Select, Spinner, TextInput, cx, toast } from "./ui";
import { useChannels } from "./channels";

/* Orphan file report: video posts the channel has but the library does not.
   The recovery path is the ordinary link path — "queue these" re-uses it. */
export default function OrphansPage() {
  const { channels } = useChannels();
  const [data, setData] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [channel, setChannel] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(new Set());

  const load = useCallback(async () => {
    try {
      const d = await api.get("/admin/api/orphans", {
        channel,
        include_resolved: false,
        page,
      });
      setData(d);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, [channel, page]);

  const loadJobs = useCallback(async () => {
    try {
      setJobs(await api.get("/admin/api/jobs"));
    } catch (e) {
      /* secondary panel */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

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

  const toggle = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (error && !data)
    return (
      <Panel>
        <AdminErrorState title="Orphan report unavailable" error={error} />
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
  const pageSize = data.page_size || 50;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const scanEnabled = jobs?.switches?.orphan_scan;
  // The report filter uses the bare id ("1234567890"), but the scan job needs
  // the full "-100…" chat id to open the channel through the bot client.
  const selectedChannel = channels.find((c) => c.clean_id === channel);
  const scanChannelId = selectedChannel ? selectedChannel.id : channel;
  const scanChannelIds = scanChannelId
    ? [scanChannelId]
    : channels.map((c) => c.id);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Gaps</p>
          <h1 className="font-display text-3xl mt-1.5">Orphan files</h1>
          <p className="text-sm text-bone/60 mt-1">
            {total.toLocaleString()} video posts with no library match
          </p>
        </div>
        {scanEnabled ? <Badge tone="ok">Scan enabled</Badge> : <Badge tone="warn">Scan disabled</Badge>}
        <Button variant="ghost" onClick={load} disabled={busy}>
          <FiRefreshCw size={14} /> Refresh
        </Button>
      </header>

      <Panel
        title="Filter"
        subtitle="Rows older than the newest completed scan of their channel are treated as resolved"
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[220px]">
            <Label htmlFor="orphan-channel">Channel</Label>
            {channels.length > 0 ? (
              <Select
                id="orphan-channel"
                value={channel}
                onChange={(e) => {
                  setPage(1);
                  setChannel(e.target.value);
                }}
              >
                <option value="">All channels</option>
                {channels.map((c) => (
                  <option key={c.index} value={c.clean_id}>
                    {c.label}
                  </option>
                ))}
              </Select>
            ) : (
              <TextInput
                id="orphan-channel"
                value={channel}
                placeholder="e.g. 1234567890"
                onChange={(e) => {
                  setPage(1);
                  setChannel(e.target.value);
                }}
              />
            )}
          </div>
          <Button variant="primary" onClick={load} disabled={busy}>
            Apply
          </Button>
          {scanEnabled ? (
            <Button
              variant="ghost"
              disabled={busy || scanChannelIds.length === 0}
              onClick={() =>
                act("Orphan scan started", () =>
                  api.post("/admin/api/jobs/start", {
                    kind: "orphan_scan",
                    params: { channels: scanChannelIds },
                  })
                )
              }
            >
              {channel ? "Scan this channel" : "Scan all channels"}
            </Button>
          ) : (
            <span className="text-[11px] text-bone/55">
              Set ORPHAN_SCAN=true on the server to scan for orphans.
            </span>
          )}
        </div>
      </Panel>

      <Panel
        title="Orphans"
        subtitle="Select the posts you want back in the library"
        actions={
          <Button
            variant="primary"
            disabled={busy || selected.size === 0}
            onClick={() =>
              act(
                "Orphans queued",
                async () => {
                  await api.post("/admin/api/orphans/queue", { ids: Array.from(selected) });
                  setSelected(new Set());
                }
              )
            }
          >
            <FiUpload size={14} /> Queue {selected.size || ""} selected
          </Button>
        }
      >
        {items.length === 0 ? (
          <EmptyState title="No orphan posts">
            Every video post in the scanned channels is already in the library.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto -mx-1 px-1">
            <table className="w-full text-sm min-w-[680px]">
              <thead>
                <tr className="text-left">
                  {["", "Channel", "Message", "Title", "Scanned", ""].map((h, i) => (
                    <th
                      key={`${h}-${i}`}
                      className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 pb-2 pr-3 font-normal"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => {
                  const isSelected = selected.has(row.id);
                  return (
                    <tr
                      key={row.id}
                      className={cx("border-t border-bone/6", isSelected && "bg-projector/5")}
                    >
                      <td className="py-2.5 pr-3">
                        <input
                          type="checkbox"
                          aria-label={`Select message ${row.message_id}`}
                          checked={isSelected}
                          onChange={() => toggle(row.id)}
                          className="accent-[#e9b872] w-4 h-4"
                        />
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/60">
                        {row.channel}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/60 tabular-nums">
                        {row.message_id}
                      </td>
                      <td className="py-2.5 pr-3">
                        <p className="text-bone truncate max-w-[280px]">
                          {row.title || "Untitled"}
                        </p>
                        {row.size ? (
                          <p className="timecode text-[10px] text-bone/55">
                            {row.size} bytes
                          </p>
                        ) : null}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/50">
                        {row.scanned_at ? new Date(row.scanned_at).toLocaleString() : "—"}
                      </td>
                      <td className="py-2.5 text-right">
                        <a
                          href={`https://t.me/c/${String(row.channel).replace(/^-/, "")}/${row.message_id}`}
                          target="_blank"
                          rel="noreferrer"
                          className="timecode text-[11px] text-projector hover:underline"
                        >
                          open
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {items.length > 0 ? (
          <div className="mt-4 pt-4 border-t border-bone/6 flex items-center justify-between">
            <p className="timecode text-[10px] text-bone/60">
              page {data.page || page} of {pages} · {total.toLocaleString()} rows
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                disabled={busy || page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                <FiChevronLeft size={14} /> Newer
              </Button>
              <Button
                variant="ghost"
                disabled={busy || page >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Older <FiChevronRight size={14} />
              </Button>
            </div>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
