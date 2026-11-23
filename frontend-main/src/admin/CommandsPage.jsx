import React, { useState } from "react";
import { FiCheck, FiRefreshCw, FiSave, FiZap } from "react-icons/fi";
import { api } from "./api";
import { useChannels } from "./channels";
import { Button, EmptyState, Field, Label, Panel, Select, Spinner, TextInput, toast } from "./ui";

export default function CommandsPage() {
  const { channels } = useChannels();
  const [defaultId, setDefaultId] = useState("");
  const [rescan, setRescan] = useState({ channel_index: 0, limit: 100 });
  const [merge, setMerge] = useState(null);
  const [busy, setBusy] = useState("");

  const run = async (key, fn, ok) => {
    setBusy(key);
    try {
      const res = await fn();
      if (ok) toast(ok);
      return res;
    } catch (e) {
      toast(e.message, "error");
      return null;
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="space-y-5">
      <header>
        <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Operations</p>
        <h1 className="font-display text-3xl mt-1.5">Commands</h1>
        <p className="text-sm text-bone/60 mt-1">Bot-wide switches that take effect on the running backend.</p>
      </header>

      <div className="grid lg:grid-cols-2 gap-5">
        <Panel title="Default metadata ID" subtitle="Overrides TMDB lookups for every new upload">
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Label htmlFor="cmd-default">Default id</Label>
              <TextInput
                id="cmd-default"
                value={defaultId}
                onChange={(e) => setDefaultId(e.target.value)}
                placeholder="e.g. 12345 or blank to disable"
              />
            </div>
            <Button
              variant="primary"
              disabled={!!busy}
              onClick={() =>
                run("set", () => api.post("/admin/api/commands/set", { default_id: defaultId || null }), "Default id applied")
              }
            >
              {busy === "set" ? <Spinner /> : <FiSave size={14} />} Save
            </Button>
            <Button
              variant="ghost"
              disabled={!!busy}
              onClick={() => {
                setDefaultId("");
                run("clear", () => api.post("/admin/api/commands/set", { default_id: null }), "Default id cleared");
              }}
            >
              Disable
            </Button>
          </div>
          <p className="text-[11px] text-bone/55 mt-3">
            Applies to the live process only — a backend restart restores the configured value.
          </p>
        </Panel>

        <Panel title="Rescan a channel" subtitle="Finds files the bot missed, then queues them">
          <div className="flex items-end gap-2 flex-wrap">
            <div className="w-56">
              <Label htmlFor="cmd-channel">Channel</Label>
              {channels.length > 0 ? (
                <Select
                  id="cmd-channel"
                  value={String(rescan.channel_index)}
                  onChange={(e) =>
                    setRescan({ ...rescan, channel_index: Number(e.target.value) })
                  }
                >
                  {channels.map((c) => (
                    <option key={c.index} value={c.index}>
                      {c.label}
                    </option>
                  ))}
                </Select>
              ) : (
                <TextInput
                  id="cmd-channel"
                  type="number"
                  value={rescan.channel_index}
                  onChange={(e) =>
                    setRescan({ ...rescan, channel_index: Number(e.target.value) })
                  }
                />
              )}
            </div>
            <div className="w-28">
              <Label htmlFor="cmd-limit">Message limit</Label>
              <TextInput
                id="cmd-limit"
                type="number"
                value={rescan.limit}
                onChange={(e) => setRescan({ ...rescan, limit: Number(e.target.value) })}
              />
            </div>
            <Button
              variant="primary"
              className="ml-auto"
              disabled={!!busy}
              onClick={() =>
                run("rescan", () => api.post("/admin/api/commands/rescan", rescan), "Rescan started in the background")
              }
            >
              {busy === "rescan" ? <Spinner /> : <FiZap size={14} />} Start rescan
            </Button>
          </div>
          <p className="text-[11px] text-bone/55 mt-3">
            The user session must be valid for history scans. Watch the queue desk for new pending items.
          </p>
        </Panel>

        <Panel
          title="Merge duplicate titles"
          subtitle="Same title stored on more than one cluster"
          actions={
            <>
              <Button
                variant="ghost"
                disabled={!!busy}
                onClick={() => run("dry", () => api.post("/admin/api/merge-duplicates", {}, { apply: "false" }).then(setMerge), "Dry run complete")}
              >
                {busy === "dry" ? <Spinner /> : <FiRefreshCw size={14} />} Dry run
              </Button>
              <Button
                variant="danger"
                disabled={!!busy}
                onClick={() => run("apply", () => api.post("/admin/api/merge-duplicates", {}, { apply: "true" }).then(setMerge), "Duplicates merged")}
              >
                {busy === "apply" ? <Spinner /> : <FiCheck size={14} />} Apply merge
              </Button>
            </>
          }
        >
          {merge ? (
            <pre className="timecode text-[11px] leading-relaxed text-bone/65 bg-ink/60 border border-bone/8 rounded-md p-3 max-h-72 overflow-auto whitespace-pre-wrap break-all">
              {JSON.stringify(merge, null, 2)}
            </pre>
          ) : (
            <EmptyState title="No run yet">Start with a dry run to see what would be merged.</EmptyState>
          )}
        </Panel>

        <Panel title="Clear all caches" subtitle="Forces fresh reads on the next request">
          <div className="flex items-center gap-2">
            <Button
              variant="danger"
              disabled={!!busy}
              onClick={() => run("cache", () => api.post("/admin/api/commands/clear-cache", {}), "All caches cleared")}
            >
              {busy === "cache" ? <Spinner /> : <FiZap size={14} />} Clear everything
            </Button>
            <span className="text-[11px] text-bone/55">
              Video chunks, metadata, hot movies and the remux directory.
            </span>
          </div>
          <Field label="Targeted clear">
            <div className="flex flex-wrap gap-2 mt-1">
              {["video", "remux"].map((target) => (
                <Button
                  key={target}
                  variant="ghost"
                  disabled={!!busy}
                  onClick={() =>
                    run(`t-${target}`, () => api.post("/admin/api/cache/clear", { target }), `${target} cache cleared`)
                  }
                >
                  Clear {target}
                </Button>
              ))}
            </div>
          </Field>
        </Panel>
      </div>
    </div>
  );
}
