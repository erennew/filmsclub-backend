import React, { useCallback, useEffect, useState } from "react";
import { FiChevronLeft, FiChevronRight, FiPlay, FiRefreshCw, FiSquare } from "react-icons/fi";
import { api } from "./api";
import {
  AdminErrorState,
  Badge,
  Button,
  ConfirmModal,
  EmptyState,
  Label,
  Modal,
  Panel,
  Select,
  Spinner,
  cx,
  toast,
} from "./ui";

/* Audio-language curation plus the two bulk jobs that feed it. Both jobs are
   off by default on the server; the switches below reflect that. */
export default function CurationPage() {
  const [rows, setRows] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [minCount, setMinCount] = useState(0);
  const [sort, setSort] = useState("count:desc");
  const [page, setPage] = useState(1);
  const [flag, setFlag] = useState("no_poster");
  const [confirmRemediation, setConfirmRemediation] = useState(false);
  const [detail, setDetail] = useState(null);

  const loadCuration = useCallback(async () => {
    try {
      const d = await api.get("/admin/api/audio-curation", {
        min_count: minCount,
        sort,
        page,
        page_size: 30,
      });
      setRows(d);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, [minCount, sort, page]);

  const loadJobs = useCallback(async () => {
    try {
      setJobs(await api.get("/admin/api/jobs"));
    } catch (e) {
      /* the jobs panel is secondary — the curation list still works */
    }
  }, []);

  useEffect(() => {
    loadCuration();
  }, [loadCuration]);
  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  const act = async (label, fn) => {
    setBusy(true);
    try {
      await fn();
      toast(label);
      await Promise.all([loadCuration(), loadJobs()]);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const openDetail = async (row) => {
    setBusy(true);
    try {
      const d = await api.get(
        `/admin/api/media/${row.type}/${row.db_index}/${row.tmdb_id}`
      );
      setDetail({ row, media: d });
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  if (error && !rows)
    return (
      <Panel>
        <AdminErrorState title="Curation unavailable" error={error} />
      </Panel>
    );
  if (!rows)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );

  const items = rows.items || [];
  const total = rows.total_count || 0;
  const pages = Math.max(1, Math.ceil(total / (rows.page_size || 30)));
  const switches = jobs?.switches || {};
  const jobRows = jobs?.jobs || [];
  const flags = jobs?.flags || ["no_poster", "no_backdrop", "no_description", "no_genres", "no_cast"];

  const latestJob = (kind) => jobRows.find((j) => j.kind === kind);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Curation</p>
          <h1 className="font-display text-3xl mt-1.5">Audio languages</h1>
          <p className="text-sm text-bone/60 mt-1">
            {total.toLocaleString()} titles ranked by distinct audio languages
          </p>
        </div>
        <Button variant="ghost" onClick={() => { loadCuration(); loadJobs(); }} disabled={busy}>
          <FiRefreshCw size={14} /> Refresh
        </Button>
      </header>

      <div className="grid lg:grid-cols-2 gap-5">
        <Panel
          title="Audio-language backfill"
          subtitle="Denormalize the audio languages of every title"
          actions={
            switches.audio_lang_backfill ? (
              <Badge tone="ok">Enabled</Badge>
            ) : (
              <Badge tone="warn">Disabled</Badge>
            )
          }
        >
          <p className="text-sm text-bone/60">
            Walks every cluster and both media collections, writing
            <code className="text-bone/80"> audio_languages</code> and
            <code className="text-bone/80"> audio_language_count</code> onto each title.
            A title that was never probed stays at zero — the job reports how
            many, so the gap is a number rather than a surprise.
          </p>
          {latestJob("audio_lang_backfill") ? (
            <JobSummary job={latestJob("audio_lang_backfill")} />
          ) : null}
          <div className="mt-4 flex items-center gap-2">
            <Button
              variant="primary"
              disabled={busy || !switches.audio_lang_backfill}
              onClick={() =>
                act("Backfill started", () =>
                  api.post("/admin/api/jobs/start", {
                    kind: "audio_lang_backfill",
                    params: {},
                  })
                )
              }
            >
              <FiPlay size={14} /> Start backfill
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() =>
                act("Backfill stopping", () =>
                  api.post("/admin/api/jobs/stop", { kind: "audio_lang_backfill" })
                )
              }
            >
              <FiSquare size={14} /> Stop
            </Button>
            {!switches.audio_lang_backfill ? (
              <span className="text-[11px] text-bone/55">
                Set AUDIO_LANG_BACKFILL=true on the server to enable this.
              </span>
            ) : null}
          </div>
        </Panel>

        <Panel
          title="Metadata remediation"
          subtitle="Refresh metadata for titles missing a field"
          actions={
            switches.metadata_remediation ? (
              <Badge tone="ok">Enabled</Badge>
            ) : (
              <Badge tone="warn">Disabled</Badge>
            )
          }
        >
          <div className="mb-3">
            <Label htmlFor="remediation-flag">Missing</Label>
            <Select
              id="remediation-flag"
              value={flag}
              onChange={(e) => setFlag(e.target.value)}
            >
              {flags.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </Select>
          </div>
          {latestJob("metadata_remediation") ? (
            <JobSummary job={latestJob("metadata_remediation")} />
          ) : null}
          <div className="mt-4 flex items-center gap-2">
            <Button
              variant="primary"
              disabled={busy || !switches.metadata_remediation}
              onClick={() => setConfirmRemediation(true)}
            >
              <FiPlay size={14} /> Start remediation
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() =>
                act("Remediation stopping", () =>
                  api.post("/admin/api/jobs/stop", { kind: "metadata_remediation" })
                )
              }
            >
              <FiSquare size={14} /> Stop
            </Button>
            {!switches.metadata_remediation ? (
              <span className="text-[11px] text-bone/55">
                Set METADATA_REMEDIATION=true on the server to enable this.
              </span>
            ) : null}
          </div>
        </Panel>
      </div>

      <Panel
        title="Titles"
        subtitle="A title whose audio was replaced by a thinner one is findable here"
        actions={
          <div className="flex items-center gap-2">
            <Select
              aria-label="Minimum language count"
              value={String(minCount)}
              onChange={(e) => {
                setMinCount(Number(e.target.value));
                setPage(1);
              }}
              className="w-28"
            >
              {[0, 1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n === 0 ? "All" : `${n}+`}
                </option>
              ))}
            </Select>
            <Select
              aria-label="Sort"
              value={sort}
              onChange={(e) => {
                setSort(e.target.value);
                setPage(1);
              }}
              className="w-36"
            >
              <option value="count:desc">Most languages</option>
              <option value="count:asc">Fewest languages</option>
              <option value="title:asc">Title A–Z</option>
              <option value="title:desc">Title Z–A</option>
            </Select>
          </div>
        }
      >
        {items.length === 0 ? (
          <EmptyState title="Nothing to curate yet">
            Run the audio-language backfill to populate this list.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto -mx-1 px-1">
            <table className="w-full text-sm min-w-[640px]">
              <thead>
                <tr className="text-left">
                  {["Title", "Type", "Languages", "Count", ""].map((h) => (
                    <th
                      key={h}
                      className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60 pb-2 pr-3 font-normal"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr key={`${row.db_index}-${row.type}-${row.tmdb_id}`} className="border-t border-bone/6">
                    <td className="py-2.5 pr-3">
                      <p className="text-bone truncate max-w-[280px]">{row.title || "—"}</p>
                      <p className="timecode text-[10px] text-bone/55">
                        {row.tmdb_id}
                        {row.release_year ? ` • ${row.release_year}` : ""}
                      </p>
                    </td>
                    <td className="py-2.5 pr-3">
                      <Badge>{row.type}</Badge>
                    </td>
                    <td className="py-2.5 pr-3">
                      <div className="flex flex-wrap gap-1">
                        {(row.audio_languages || []).length === 0 ? (
                          <span className="text-[11px] text-bone/55">none known</span>
                        ) : (
                          (row.audio_languages || []).map((lang) => (
                            <span
                              key={lang}
                              className="timecode text-[10px] border border-projector/35 text-projector px-1.5 py-0.5 rounded-[2px]"
                            >
                              {lang}
                            </span>
                          ))
                        )}
                      </div>
                      {(row.dropped_audio_languages || []).length ? (
                        <p className="timecode text-[10px] text-amber-200/80 mt-1">
                          capped away: {row.dropped_audio_languages.join(", ")}
                        </p>
                      ) : null}
                    </td>
                    <td className="py-2.5 pr-3 timecode text-[12px] text-bone/70 tabular-nums">
                      {row.audio_language_count ?? 0}
                    </td>
                    <td className="py-2.5 text-right">
                      <Button variant="ghost" disabled={busy} onClick={() => openDetail(row)}>
                        Files
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-4 pt-4 border-t border-bone/6 flex items-center justify-between">
          <p className="timecode text-[10px] text-bone/60">
            page {rows.page} of {pages}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              disabled={busy || rows.page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <FiChevronLeft size={14} /> Newer
            </Button>
            <Button
              variant="ghost"
              disabled={busy || rows.page >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Older <FiChevronRight size={14} />
            </Button>
          </div>
        </div>
      </Panel>

      <ConfirmModal
        open={confirmRemediation}
        onClose={() => setConfirmRemediation(false)}
        busy={busy}
        title={`Refresh metadata for every title missing "${flag}"`}
        confirmWord="RUN"
        confirmLabel="Start remediation"
        onConfirm={() =>
          act("Remediation started", () =>
            api.post("/admin/api/jobs/start", {
              kind: "metadata_remediation",
              params: { flag, media_type: "all" },
            })
          ).finally(() => setConfirmRemediation(false))
        }
      />

      <Modal
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
        title={detail?.row?.title || "Files"}
        width="max-w-3xl"
      >
        {detail ? <FileBreakdown media={detail.media} /> : null}
      </Modal>
    </div>
  );
}

function JobSummary({ job }) {
  const counts = job.counts || {};
  const entries = Object.entries(counts);
  return (
    <div className="mt-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-3">
      <div className="flex items-center gap-2">
        <Badge tone={job.status === "error" ? "bad" : job.status === "running" ? "good" : "neutral"}>
          {job.status}
        </Badge>
        {job.updated_at ? (
          <span className="timecode text-[10px] text-bone/60">{job.updated_at}</span>
        ) : null}
      </div>
      {entries.length > 0 ? (
        <dl className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-y-1 gap-x-3 text-[11px]">
          {entries.map(([key, value]) => (
            <React.Fragment key={key}>
              <dt className="timecode text-bone/60 truncate">{key}</dt>
              <dd className="timecode text-bone/80 text-right tabular-nums">{String(value)}</dd>
            </React.Fragment>
          ))}
        </dl>
      ) : null}
      {job.error ? <p className="text-[11px] text-signal mt-2">{job.error}</p> : null}
    </div>
  );
}

function FileBreakdown({ media }) {
  const files = [];
  if (media?.telegram) {
    media.telegram.forEach((q) => files.push({ file: q }));
  }
  (media?.seasons || []).forEach((season) => {
    (season.episodes || []).forEach((episode) => {
      (episode.telegram || []).forEach((q) =>
        files.push({
          file: q,
          label: `S${season.season_number}E${episode.episode_number}`,
        })
      );
    });
  });

  if (!files.length) return <p className="text-sm text-bone/50">This title has no files.</p>;

  return (
    <ul className="space-y-2">
      {files.map((entry, i) => {
        const tracks = entry.file.audio_tracks || [];
        return (
          <li
            key={i}
            className="rounded-md border border-bone/8 bg-ink/40 px-3.5 py-2.5 flex items-start gap-3"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm text-bone truncate">
                {entry.label ? `${entry.label} • ` : ""}
                {entry.file.name || entry.file.quality || "file"}
              </p>
              <div className="flex flex-wrap gap-1 mt-1">
                {tracks.length === 0 ? (
                  <span className="timecode text-[10px] text-bone/55">not probed</span>
                ) : (
                  tracks.map((t, j) => (
                    <span
                      key={j}
                      className="timecode text-[10px] border border-bone/15 text-bone/60 px-1.5 py-0.5 rounded-[2px]"
                    >
                      {t.language}
                    </span>
                  ))
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
