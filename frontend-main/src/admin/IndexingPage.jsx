import React, { useCallback, useEffect, useState } from "react";
import { FiActivity, FiPlay, FiRefreshCw, FiSquare } from "react-icons/fi";
import { api } from "./api";
import { useChannels } from "./channels";
import { AdminErrorState, Badge, Button, EmptyState, Label, Panel, Spinner, TextArea, TextInput, cx, formatEta, toast } from "./ui";

function scanTone(status) {
  if (status === "running") return "good";
  if (status === "waiting") return "warn";
  if (status === "error") return "bad";
  if (status === "completed") return "ok";
  return "neutral";
}

/* Channel indexing: the same start/stop path the bot uses, so the panel and
   the bot can never diverge. */
export default function IndexingPage() {
  const { channels } = useChannels();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [links, setLinks] = useState("");
  const [picked, setPicked] = useState([]);
  const [auto, setAuto] = useState(true);
  const [skipFirst, setSkipFirst] = useState("0");
  const [skipDefault, setSkipDefault] = useState(0);

  const toggleChannel = (id) =>
    setPicked((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );

  const load = useCallback(async () => {
    try {
      const d = await api.get("/admin/api/index-scans");
      setData(d);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    if (!auto) return undefined;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load, auto]);

  /* The runtime knob is the default for both the bot prompt and this page. */
  useEffect(() => {
    api
      .get("/admin/api/config")
      .then((d) => {
        const row = (d.knobs || []).find(
          (k) => k.key === "index_skip_first_messages"
        );
        if (row) {
          setSkipDefault(Number(row.value) || 0);
          setSkipFirst(String(row.value ?? 0));
        }
      })
      .catch(() => {
        /* an older backend simply has no knob — the field still works */
      });
  }, []);

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

  if (error && !data)
    return (
      <Panel>
        <AdminErrorState title="Indexing unavailable" error={error} />
      </Panel>
    );
  if (!data)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );

  const registry = data.registry || {};
  const scans = data.scans || [];
  const running = registry.running || [];
  const waiting = data.waiting || [];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Coverage</p>
          <h1 className="font-display text-3xl mt-1.5">Channel indexing</h1>
          <p className="text-sm text-bone/60 mt-1">
            cap {data.cap} scan{data.cap === 1 ? "" : "s"} · {running.length} running ·{" "}
            {waiting.length} waiting{auto ? " · refreshing every 5s" : ""}
          </p>
        </div>
        <Badge tone={running.length ? "good" : "neutral"}>
          {running.length ? "Scanning" : "Idle"}
        </Badge>
        <Button variant="ghost" onClick={() => setAuto((v) => !v)}>
          <FiActivity size={14} /> {auto ? "Auto-refresh on" : "Auto-refresh off"}
        </Button>
        <Button variant="ghost" onClick={load} disabled={busy}>
          <FiRefreshCw size={14} />
        </Button>
      </header>

      <div className="grid lg:grid-cols-2 gap-5">
        <Panel
          title="Start a scan"
          subtitle="Paste t.me post links, one per line"
          actions={
            <Button
              variant="danger"
              disabled={busy || (running.length === 0 && waiting.length === 0)}
              onClick={() =>
                act("All scans stopping", () => api.post("/admin/api/index-scans/stop", {}))
              }
            >
              <FiSquare size={14} /> Stop all
            </Button>
          }
        >
          <Label>Or pick configured channels</Label>
          {channels.length > 0 ? (
            <div className="flex flex-wrap gap-2 mb-3">
              {channels.map((c) => {
                const active = picked.includes(c.id);
                return (
                  <button
                    key={c.index}
                    type="button"
                    aria-pressed={active}
                    onClick={() => toggleChannel(c.id)}
                    className={cx(
                      "rounded-md border px-2.5 py-1.5 timecode text-[11px] transition-colors",
                      active
                        ? "border-projector/60 bg-projector/12 text-projector"
                        : "border-bone/12 bg-ink/40 text-bone/55 hover:text-bone"
                    )}
                  >
                    {active ? "✓ " : ""}{c.label}
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="text-[11px] text-bone/55 mb-3">
              No channels listed by the backend — paste post links below instead.
            </p>
          )}

          <Label htmlFor="index-links">Links (optional)</Label>
          <TextArea
            id="index-links"
            value={links}
            onChange={(e) => setLinks(e.target.value)}
            rows={4}
            className="font-mono text-[12px]"
            placeholder={"https://t.me/c/123456/789\nhttps://t.me/mychannel/42"}
          />
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <div>
              <Label htmlFor="index-skip">Skip newest messages</Label>
              <TextInput
                id="index-skip"
                type="number"
                min="0"
                max="100000"
                step="1"
                value={skipFirst}
                onChange={(e) => setSkipFirst(e.target.value)}
                className="w-32 text-right tabular-nums"
                aria-label="Skip newest messages"
              />
            </div>
            <p className="text-[11px] text-bone/60 max-w-[340px] pb-2">
              Ignore the newest N posts in each channel (pinned promos, welcome
              posts). 0 indexes everything · runtime default {skipDefault}. The
              answer is saved with the scan and survives a restart.
            </p>
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Button
              variant="primary"
              disabled={busy || (!links.trim() && picked.length === 0)}
              onClick={() => {
                const raw = String(skipFirst).trim();
                const skip = raw === "" ? skipDefault : Number(raw);
                if (!Number.isInteger(skip) || skip < 0 || skip > 100000) {
                  toast("Skip count must be a whole number between 0 and 100000.", "error");
                  return;
                }
                act("Scan started", async () => {
                  await api.post("/admin/api/index-scans/start", {
                    links,
                    channels: picked,
                    mode: "index",
                    skip_first: skip,
                  });
                  setLinks("");
                  setPicked([]);
                });
              }}
            >
              <FiPlay size={14} /> Start indexing
            </Button>
            <span className="text-[11px] text-bone/55">
              Scans beyond the cap wait for a free slot.
            </span>
          </div>
        </Panel>

        <Panel title="Concurrency" subtitle="How many channels may be scanned at once">
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm text-bone">Maximum concurrent scans</p>
                <p className="timecode text-[10px] text-bone/60 mt-0.5">
                  All scans share one read budget, so raising this does not raise
                  the total Telegram read rate.
                </p>
              </div>
              <Badge tone="good">cap {data.cap}</Badge>
            </div>
            <p className="text-[11px] text-bone/55">
              Change it on the Runtime settings page — the value is live and takes
              effect on the next launch.
            </p>
          </div>
        </Panel>
      </div>

      {waiting.length > 0 && (
        <Panel title="Waiting for a slot" subtitle="Confirmed but not started yet">
          <ul className="space-y-1.5">
            {waiting.map((row) => (
              <li
                key={row.channel_id}
                className="flex items-center gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-2.5"
              >
                <span className="timecode text-[11px] text-bone/60 w-32 shrink-0 truncate">
                  {row.channel_id}
                </span>
                <span className="text-sm text-bone/70 truncate flex-1">{row.title || "—"}</span>
                <Badge tone="warn">waiting</Badge>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel title="Scans" subtitle="One durable state per channel">
        {scans.length === 0 ? (
          <EmptyState title="No scan has run yet">
            Send a t.me post link in DM, or start one from above.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto -mx-1 px-1">
            <table className="w-full text-sm min-w-[840px]">
              <thead>
                <tr className="text-left">
                  {["Channel", "Status", "Checked", "Skipped", "Queued", "Existing", "Errors", "Cursor", "Elapsed", ""].map(
                    (h) => (
                      <th
                        key={h}
                        className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 pb-2 pr-3 font-normal"
                      >
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody>
                {scans.map((scan) => {
                  const cursor = scan.next_offset_id;
                  return (
                    <tr key={scan.channel_id} className="border-t border-bone/6">
                      <td className="py-2.5 pr-3">
                        <p className="timecode text-[11px] text-bone/75">{scan.channel_id}</p>
                        <p className="text-[11px] text-bone/60 truncate max-w-[180px]">
                          {scan.title || "—"}
                        </p>
                      </td>
                      <td className="py-2.5 pr-3">
                        <Badge tone={scanTone(scan.status)}>{scan.status}</Badge>
                        {scan.mode === "audit" ? (
                          <Badge className="ml-1">audit</Badge>
                        ) : null}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/60 tabular-nums">
                        {scan.checked ?? 0}
                      </td>
                      <td
                        className="py-2.5 pr-3 timecode text-[11px] text-bone/60 tabular-nums"
                        title={`Requested skip: ${scan.skip_first ?? 0}`}
                      >
                        {scan.skipped ?? 0}
                        {scan.skip_first ? (
                          <span className="text-bone/55"> / {scan.skip_first}</span>
                        ) : null}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/60 tabular-nums">
                        {scan.queued ?? 0}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/60 tabular-nums">
                        {scan.existing ?? 0}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/60 tabular-nums">
                        {scan.errors ?? 0}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/50 tabular-nums">
                        {cursor ? `#${cursor}` : "—"}
                      </td>
                      <td className="py-2.5 pr-3 timecode text-[11px] text-bone/50 tabular-nums">
                        {formatEta(scan.elapsed_seconds)}
                      </td>
                      <td className="py-2.5 text-right">
                        <Button
                          variant="ghost"
                          disabled={
                            busy
                            || !(running.includes(scan.channel_id)
                              || scan.status === "waiting")
                          }
                          onClick={() =>
                            act(
                              "Scan stopping",
                              () =>
                                api.post("/admin/api/index-scans/stop", {
                                  channel_id: scan.channel_id,
                                })
                            )
                          }
                        >
                          <FiSquare size={13} /> Stop
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
