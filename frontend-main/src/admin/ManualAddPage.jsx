import React, { useState } from "react";
import { FiLink, FiPlus, FiRotateCcw, FiSearch, FiShuffle } from "react-icons/fi";
import { api } from "./api";
import { useChannels, channelLink } from "./channels";
import {
  Badge,
  Button,
  EmptyState,
  Label,
  Panel,
  Select,
  Spinner,
  TextInput,
  toast,
} from "./ui";

/* Single-post ingestion. Two modes:
   - "add": queue a post with explicit TMDB/IMDb metadata, bypassing the
     filename parser (for releases the automatic pipeline could not identify).
   - "reassign": the file IS already in the library but under the wrong title
     or episode. Look it up, then move the existing quality entry so its
     encoded stream id (and every link already handed out) keeps working. */

const EMPTY = {
  link: "",
  media_ref: "",
  kind: "",
  quality: "",
  rip: "",
  languages: "",
  codec: "",
  season: "",
  episode: "",
  replace_existing_quality: false,
  remove_empty_title: true,
};

const rowKey = (r) =>
  [r.db_index, r.tmdb_id, r.collection, r.season_number, r.episode_number, r.file_id].join("|");

function ResultCard({ result }) {
  const reassigned = Boolean(result.from);
  const badge = reassigned
    ? result.moved ? "Reassigned" : "Partially moved"
    : result.queued ? "Queued" : result.duplicate ? "Already in library" : "Rejected";
  const tone = reassigned
    ? result.moved ? "good" : "warn"
    : result.queued ? "good" : result.duplicate ? "warn" : "bad";

  return (
    <div className="mt-4 rounded-md border border-bone/10 bg-ink/50 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{badge}</Badge>
        <p className="text-sm text-bone font-medium">{result.title || "Untitled"}</p>
        {result.media_type ? <Badge>{result.media_type}</Badge> : null}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-y-2 gap-x-4 text-[12px]">
        {[
          ["TMDB id", result.tmdb_id],
          ["Quality", result.quality],
          ["Rip", result.rip],
          ["Languages", Array.isArray(result.languages) ? result.languages.join(", ") : result.languages],
          ["Codec", result.codec],
          ["Channel / post", result.channel && result.message_id
            ? `${result.channel} / ${result.message_id}` : null],
          ["Season / episode", result.season_number != null
            ? `${result.season_number} / ${result.episode_number}` : null],
          ["File", result.file_name],
          ["Moved from", reassigned ? result.from.title : null],
          ["Old title removed", reassigned ? (result.old_title_removed ? "yes" : "no") : null],
          ["Replaced file", reassigned ? result.replaced_file_id : null],
        ].map(([k, v]) =>
          v === null || v === undefined || v === "" ? null : (
            <React.Fragment key={k}>
              <dt className="timecode text-[10px] uppercase tracking-[0.14em] text-bone/60">{k}</dt>
              <dd className="timecode text-bone/80 text-right break-all">{String(v)}</dd>
            </React.Fragment>
          )
        )}
      </dl>
      <div className="mt-3 flex flex-wrap gap-2">
        {result.tmdb_url ? (
          <a
            href={result.tmdb_url}
            target="_blank"
            rel="noreferrer"
            className="timecode text-[11px] text-projector hover:underline"
          >
            Open on TMDB
          </a>
        ) : null}
        {result.channel && result.message_id ? (
          <a
            href={`https://t.me/c/${String(result.channel).replace(/^-/, "")}/${result.message_id}`}
            target="_blank"
            rel="noreferrer"
            className="timecode text-[11px] text-projector hover:underline"
          >
            Open Telegram post
          </a>
        ) : null}
      </div>
      {(result.detail || result.warning) ? (
        <p className="mt-3 text-[11px] text-bone/50">{result.detail || result.warning}</p>
      ) : null}
    </div>
  );
}

function MatchCard({ row, chosen, onChoose }) {
  return (
    <label
      className={`flex items-start gap-3 rounded-md border px-3.5 py-3 cursor-pointer transition-colors ${
        chosen ? "border-projector/50 bg-projector/5" : "border-bone/10 bg-ink/40 hover:border-bone/25"
      }`}
    >
      <input
        type="radio"
        name="reassign-source"
        className="mt-1 accent-[#e9b872]"
        checked={chosen}
        onChange={onChoose}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-bone truncate">{row.title || "Untitled"}</p>
        <p className="timecode text-[10px] text-bone/60 mt-0.5 uppercase tracking-wider break-all">
          {row.collection} · tmdb {row.tmdb_id} · cluster {row.db_index}
          {row.season_number != null ? ` · S${row.season_number}E${row.episode_number}` : ""}
          {row.stored_quality ? ` · ${row.stored_quality}` : ""}
        </p>
        <p className="timecode text-[10px] text-bone/55 mt-1 break-all">
          {row.file_name || row.file_id}
        </p>
      </div>
    </label>
  );
}

export default function ManualAddPage() {
  const { channels, error: channelsError } = useChannels();
  const [mode, setMode] = useState("add");
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [looking, setLooking] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [lookup, setLookup] = useState(null);
  const [selected, setSelected] = useState("");

  const set = (key) => (e) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: value }));
    if (key === "link") {
      setLookup(null);
      setSelected("");
    }
  };

  const onChannel = (e) => {
    const row = channels.find((c) => String(c.index) === e.target.value);
    if (!row) return;
    const prefix = channelLink(row).replace(/\/\d+$/, "/");
    setForm((f) => ({ ...f, link: prefix }));
    setLookup(null);
    setSelected("");
  };

  const switchMode = (next) => {
    setMode(next);
    setError("");
    setResult(null);
    setLookup(null);
    setSelected("");
  };

  const doLookup = async () => {
    if (!form.link.trim()) {
      setError("Paste the Telegram post link first.");
      return;
    }
    setLooking(true);
    setError("");
    setResult(null);
    try {
      const data = await api.post("/admin/api/files/lookup", { link: form.link.trim() });
      setLookup(data);
      setSelected(data.matches?.length ? rowKey(data.matches[0]) : "");
      if (!data.matches?.length) {
        toast("That file is not in the library — use Add a new file instead.", "error");
      }
    } catch (err) {
      setError(err.message);
      toast(err.message, "error");
    } finally {
      setLooking(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!form.media_ref.trim()) {
      setError("A TMDB/IMDb reference is required.");
      return;
    }
    if (mode === "add" && !form.link.trim()) {
      setError("A Telegram post link is required.");
      return;
    }
    const match = lookup?.matches?.find((r) => rowKey(r) === selected) || lookup?.matches?.[0];
    if (mode === "reassign" && !match) {
      setError("Look the file up first so we know which entry to move.");
      return;
    }

    setBusy(true);
    setError("");
    setResult(null);
    try {
      const payload = {
        media_ref: form.media_ref.trim(),
        kind: form.kind || null,
        quality: form.quality.trim() || null,
        rip: form.rip.trim() || null,
        languages: form.languages.trim() || null,
        codec: form.codec.trim() || null,
        season: form.season === "" ? null : Number(form.season),
        episode: form.episode === "" ? null : Number(form.episode),
      };
      const data = mode === "reassign"
        ? await api.post("/admin/api/files/reassign", {
            ...payload,
            // The lookup already resolved the stored id. Send only that: the
            // original Telegram post may since have been edited or deleted.
            file_id: match.file_id,
            source: {
              db_index: match.db_index,
              tmdb_id: match.tmdb_id,
              collection: match.collection,
              season_number: match.season_number,
              episode_number: match.episode_number,
            },
            replace_existing_quality: Boolean(form.replace_existing_quality),
            remove_empty_title: Boolean(form.remove_empty_title),
          })
        : await api.post("/admin/api/queue/manual", {
            ...payload,
            link: form.link.trim(),
          });
      setResult(data);
      if (mode === "reassign") {
        setLookup(null);
        setSelected("");
        toast(data.moved ? "File reassigned" : "Attached — check the warning", data.moved ? "success" : "error");
      } else {
        toast(data.queued ? "Post queued" : data.duplicate ? "Already in library" : "Not queued",
          data.queued ? "success" : "error");
      }
    } catch (err) {
      setError(err.message);
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">Ingest</p>
          <h1 className="font-display text-3xl mt-1.5">Manual add</h1>
          <p className="text-sm text-bone/60 mt-1">
            {mode === "add"
              ? "Queue one Telegram post with explicit TMDB/IMDb metadata, bypassing filename parsing."
              : "Move a file that was indexed under the wrong title or episode to the right one."}
          </p>
        </div>
        <div className="flex rounded-md border border-bone/12 overflow-hidden">
          {[
            ["add", "Add a new file", FiPlus],
            ["reassign", "Reassign existing", FiShuffle],
          ].map(([id, label, Icon]) => (
            <button
              key={id}
              type="button"
              onClick={() => switchMode(id)}
              className={`inline-flex items-center gap-2 px-3.5 py-2 text-sm transition-colors ${
                mode === id ? "bg-projector text-ink font-semibold" : "bg-bone/5 text-bone/60 hover:text-bone"
              }`}
            >
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>
      </header>

      <form onSubmit={submit} className="grid lg:grid-cols-2 gap-5">
        <Panel title="The post" subtitle="Where the file lives">
          <div className="space-y-3">
            {channels.length > 0 ? (
              <div>
                <Label htmlFor="ma-channel">Pick a channel</Label>
                <Select id="ma-channel" defaultValue="" onChange={onChannel}>
                  <option value="">Choose to prefill the link…</option>
                  {channels.map((c) => (
                    <option key={c.index} value={c.index}>
                      {c.label}
                    </option>
                  ))}
                </Select>
              </div>
            ) : (
              <p className="text-[11px] text-bone/55">
                {channelsError
                  ? `Channel list unavailable: ${channelsError}`
                  : "No channels configured on the backend — paste the full post link below."}
              </p>
            )}
            <div>
              <Label htmlFor="ma-link">Telegram post link</Label>
              <TextInput
                id="ma-link"
                value={form.link}
                onChange={set("link")}
                placeholder="https://t.me/c/1234567890/42"
                autoCapitalize="none"
                spellCheck={false}
                className="font-mono text-[12px]"
              />
              <p className="text-[11px] text-bone/55 mt-1.5">
                Must be a direct post link (ends with the message number).
              </p>
            </div>

            {mode === "reassign" ? (
              <div className="rounded-md border border-bone/10 bg-ink/40 p-3.5 space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm text-bone/80 font-medium">Where is it now?</p>
                  <Button type="button" variant="ghost" disabled={looking} onClick={doLookup}>
                    {looking ? <Spinner /> : <FiSearch size={14} />} Look up
                  </Button>
                </div>
                {lookup ? (
                  lookup.matches?.length ? (
                    <div className="space-y-2">
                      <p className="text-[11px] text-bone/50">
                        {lookup.count} librar{lookup.count === 1 ? "y entry" : "y entries"} point at this
                        file. Pick the one to move.
                      </p>
                      {lookup.matches.map((row) => (
                        <MatchCard
                          key={rowKey(row)}
                          row={row}
                          chosen={rowKey(row) === selected}
                          onChoose={() => setSelected(rowKey(row))}
                        />
                      ))}
                    </div>
                  ) : (
                    <p className="text-[11px] text-bone/50">
                      No library entry for this post. Switch to <b>Add a new file</b> to index it.
                    </p>
                  )
                ) : (
                  <p className="text-[11px] text-bone/55">
                    Look up the post to see which title (and episode) currently holds the file.
                  </p>
                )}
              </div>
            ) : null}

            <div>
              <Label htmlFor="ma-ref">
                {mode === "reassign" ? "Correct TMDB / IMDb reference" : "TMDB / IMDb reference"}
              </Label>
              <TextInput
                id="ma-ref"
                value={form.media_ref}
                onChange={set("media_ref")}
                placeholder="603 · themoviedb.org/movie/603 · imdb.com/title/tt0133093 · imdb:tt0133093"
                autoCapitalize="none"
                spellCheck={false}
                className="font-mono text-[12px]"
              />
              <p className="text-[11px] text-bone/55 mt-1.5">
                TMDB id, full TMDB/IMDb link, or an <span className="kbd">imdb:tt…</span> ref.
                IMDb ids are resolved through TMDB.
              </p>
            </div>
            <div>
              <Label htmlFor="ma-kind">Kind</Label>
              <Select id="ma-kind" value={form.kind} onChange={set("kind")}>
                <option value="">Auto (from the link/reference)</option>
                <option value="movie">Movie</option>
                <option value="tv">Series</option>
              </Select>
            </div>
          </div>
        </Panel>

        <Panel title="File details" subtitle="Optional — anything left blank keeps the stored value / TMDB default">
          <div className="grid grid-cols-2 gap-3">
            {[
              ["quality", "Quality", "1080p"],
              ["rip", "Rip", "WEB-DL"],
              ["languages", "Languages", "ta, en"],
              ["codec", "Codec", "x265"],
            ].map(([key, label, placeholder]) => (
              <div key={key}>
                <Label htmlFor={`ma-${key}`}>{label}</Label>
                <TextInput
                  id={`ma-${key}`}
                  value={form[key]}
                  onChange={set(key)}
                  placeholder={placeholder}
                  autoCapitalize="none"
                  spellCheck={false}
                />
              </div>
            ))}
            <div>
              <Label htmlFor="ma-season">Season (TV)</Label>
              <TextInput
                id="ma-season"
                type="number"
                /* min="0": season 0 is TMDB's Specials season and is valid. */
                min="0"
                value={form.season}
                onChange={set("season")}
                placeholder="1"
              />
            </div>
            <div>
              <Label htmlFor="ma-episode">Episode (TV)</Label>
              <TextInput
                id="ma-episode"
                type="number"
                min="1"
                value={form.episode}
                onChange={set("episode")}
                placeholder="1"
              />
            </div>
          </div>
          <p className="text-[11px] text-bone/55 mt-3">
            Series require both a season and an episode number. Season{" "}
            <span className="kbd">0</span> is TMDB&apos;s Specials season and is valid.
            {mode === "reassign" ? " Quality left blank keeps the quality the file already has." : ""}
          </p>

          {mode === "add" ? (
            <p className="text-[11px] text-bone/55 mt-2">
              One post = one file. To give a title several qualities, queue the
              same title and season/episode again with the next post link — the
              new file is appended alongside the existing ones. A separate post
              counting as the same quality as one already stored replaces that
              entry, so label each quality distinctly (720p / 1080p / 2160p).
              Titles are capped by <span className="kbd">Max qualities per title</span>{" "}
              in Settings (default 6); beyond the cap the lowest-ranked quality is
              dropped and remembered as a language-coverage gap.
            </p>
          ) : null}

          {mode === "reassign" ? (
            <div className="mt-4 pt-4 border-t border-bone/8 space-y-2.5">
              <label className="flex items-start gap-2.5 text-[12px] text-bone/65 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-[#e9b872]"
                  checked={form.replace_existing_quality}
                  onChange={set("replace_existing_quality")}
                />
                <span>
                  Replace a same-quality file already on the target.
                  <span className="block text-bone/55 mt-0.5">
                    Off by default — the move is refused rather than silently dropping another file.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2.5 text-[12px] text-bone/65 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-[#e9b872]"
                  checked={form.remove_empty_title}
                  onChange={set("remove_empty_title")}
                />
                <span>
                  Delete the wrong title if it has no files left.
                  <span className="block text-bone/55 mt-0.5">
                    Prevents empty ghost entries in the library.
                  </span>
                </span>
              </label>
            </div>
          ) : null}
        </Panel>

        <div className="lg:col-span-2">
          <Panel>
            {error ? (
              <p role="alert" className="mb-3 rounded-md border border-signal/40 bg-signal/8 px-3.5 py-2.5 text-sm text-signal">
                {error}
              </p>
            ) : null}
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? <Spinner /> : mode === "reassign" ? <FiShuffle size={14} /> : <FiPlus size={14} />}
                {mode === "reassign" ? "Reassign file" : "Queue post"}
              </Button>
              {mode === "add" ? (
                /* Adding a second/third quality means repeating the same title
                   and season/episode with a different post link. Keeping the
                   metadata and clearing only the link turns a five-field retype
                   into one paste. */
                <Button
                  type="button"
                  variant="ghost"
                  disabled={busy}
                  title="Keep this title, season and episode — clear only the post link"
                  onClick={() => {
                    setForm((f) => ({ ...f, link: "" }));
                    setResult(null);
                    setError("");
                  }}
                >
                  <FiPlus size={14} /> Same title, another file
                </Button>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setForm(EMPTY);
                  setResult(null);
                  setLookup(null);
                  setSelected("");
                  setError("");
                }}
              >
                <FiRotateCcw size={14} /> Reset
              </Button>
              <span className="text-[11px] text-bone/55 ml-auto">
                <FiLink size={12} className="inline mr-1" />
                {mode === "reassign"
                  ? "The Telegram post is never touched; the stream link stays valid."
                  : "Only posts from AUTH_CHANNEL channels are accepted."}
              </span>
            </div>
            {result ? (
              <ResultCard result={result} />
            ) : !busy && !error ? (
              <div className="mt-3">
                <EmptyState title={mode === "reassign" ? "No file moved yet" : "No post queued yet"}>
                  {mode === "reassign"
                    ? "Paste the post link, look it up to see the wrong title, then give the correct IMDb/TMDB reference."
                    : "Fill in the link and a metadata reference, then queue it. Duplicates are detected before anything is written."}
                </EmptyState>
              </div>
            ) : null}
          </Panel>
        </div>
      </form>
    </div>
  );
}
