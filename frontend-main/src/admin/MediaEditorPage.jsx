import React, { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { FiArrowLeft, FiCheck, FiRefreshCw, FiSave, FiTrash2, FiExternalLink, FiShuffle } from "react-icons/fi";
import { api } from "./api";
import {
  Badge,
  Button,
  ConfirmModal,
  EmptyState,
  Field,
  Label,
  Modal,
  Panel,
  Select,
  Spinner,
  TextArea,
  TextInput,
  cx,
  toast,
} from "./ui";

const TABS = [
  { id: "details", label: "Details" },
  { id: "files", label: "Files" },
  { id: "subs", label: "Subtitles" },
  { id: "episodes", label: "Episodes", tvOnly: true },
  { id: "images", label: "Images" },
  { id: "cast", label: "Cast" },
  { id: "danger", label: "Danger zone" },
];

const blankForm = {
  title: "",
  release_year: "",
  rating: "",
  runtime: "",
  rip: "",
  languages: "",
  genres: "",
  description: "",
  poster: "",
  backdrop: "",
  cast: "",
};

const toForm = (m) => ({
  ...blankForm,
  title: m.title || "",
  release_year: m.release_year ?? "",
  rating: m.rating ?? "",
  runtime: m.type === "tv" ? `${m.total_seasons || 0} seasons / ${m.total_episodes || 0} episodes` : m.runtime || "",
  rip: m.rip || "",
  languages: (m.languages || []).join(", "),
  genres: (m.genres || []).join(", "),
  description: m.description || "",
  poster: m.poster || "",
  backdrop: m.backdrop || "",
  cast: (m.cast || [])
    .map((c) =>
      [c.name, c.character || c.role, c.profile_path || c.profile || c.image, c.id]
        .filter((v) => v !== undefined && v !== null)
        .join(" | ")
    )
    .join("\n"),
});

/* Flatten every quality file, tagging series files with their S/E position. */
function flattenQualities(media) {
  if (!media) return [];
  if ((media.type || "movie") !== "tv") return media.telegram || [];
  const out = [];
  (media.seasons || []).forEach((s) =>
    (s.episodes || []).forEach((ep) =>
      (ep.telegram || []).forEach((q) =>
        out.push({ ...q, _season: s.season_number, _episode: ep.episode_number, _episodeTitle: ep.title })
      )
    )
  );
  return out;
}

/* Season 0 is a legitimate season number (TMDB uses it for Specials), so every
 * test here has to be `!= null` rather than truthiness — the old `_season ?`
 * form dropped season/episode context for Specials, which made edits and
 * deletes target the wrong document. */
const hasSeason = (q) => q != null && q._season != null && q._season !== "";
const epParams = (q) =>
  hasSeason(q) ? { season_number: q._season, episode_number: q._episode } : {};

export default function MediaEditorPage() {
  const { type, dbIndex, tmdbId } = useParams();
  const navigate = useNavigate();
  const base = `/admin/api/media/${type}/${dbIndex}/${tmdbId}`;
  const [media, setMedia] = useState(null);
  const [form, setForm] = useState(blankForm);
  const [tab, setTab] = useState("details");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState(null);
  const [draft, setDraft] = useState(null);
  const [deleteWord, setDeleteWord] = useState("");
  const [moveDraft, setMoveDraft] = useState(null);

  const qualities = useMemo(() => flattenQualities(media), [media]);
  const isTv = media?.type === "tv";

  const load = async (showSpinner = true) => {
    if (showSpinner) setLoading(true);
    try {
      const m = await api.get(base);
      setMedia(m);
      setForm(toForm(m));
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    setDeleteWord("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base]);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const withBusy = (fn) => async (...args) => {
    setBusy(true);
    try {
      await fn(...args);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const saveDetails = withBusy(async () => {
    const payload = {
      title: form.title,
      release_year: form.release_year,
      rating: form.rating,
      rip: form.rip,
      languages: form.languages,
      genres: form.genres,
      description: form.description,
      cast: form.cast,
    };
    if (isTv) {
      payload.poster = form.poster;
      payload.backdrop = form.backdrop;
    } else {
      payload.runtime = form.runtime;
    }
    const d = await api.patch(base, payload);
    setMedia(d.media);
    setForm(toForm(d.media));
    toast("Details saved");
  });

  const saveImages = withBusy(async () => {
    const d = await api.patch(base, { poster: form.poster, backdrop: form.backdrop });
    setMedia(d.media);
    toast("Images saved");
  });

  const refreshTmdb = withBusy(async (overwrite) => {
    const d = await api.post(`${base}/refresh-metadata`, { overwrite });
    const updated = d.media || d;
    setMedia(updated);
    setForm(toForm(updated));
    toast(
      d.updated_fields && d.updated_fields.length
        ? `TMDB refreshed ${d.updated_fields.length} field(s)`
        : "Nothing to update from TMDB"
    );
  });

  const saveFile = withBusy(async () => {
    await api.patch(`${base}/files/${encodeURIComponent(draft.id)}`, 
      { quality: draft.quality, size: draft.size, name: draft.name },
      epParams(draft)
    );
    setModal(null);
    await load(false);
    toast("File updated");
  });

  const deleteFile = withBusy(async (telegramToo) => {
    const res = await api.delete(`${base}/files/${encodeURIComponent(draft.id)}`, {
      ...epParams(draft),
      ...(telegramToo ? { delete_telegram: "true" } : {}),
    });
    setModal(null);
    await load(false);
    toast(telegramToo ? `File deleted • Telegram ${res.telegram_deleted || 0}/${res.telegram_total || 0}` : "File removed from DB only");
  });

  const moveFile = withBusy(async () => {
    if (!moveDraft?.media_ref?.trim()) {
      toast("Paste the correct TMDB/IMDb reference first", "error");
      return;
    }
    const source = { db_index: dbIndex, tmdb_id: tmdbId };
    if (type === "movie" || type === "tv") source.collection = type;
    if (draft._season != null) {
      source.season_number = draft._season;
      source.episode_number = draft._episode;
    }
    const res = await api.post("/admin/api/files/reassign", {
      file_id: draft.id,
      media_ref: moveDraft.media_ref.trim(),
      kind: moveDraft.kind || null,
      quality: moveDraft.quality.trim() || null,
      season: moveDraft.season === "" ? null : Number(moveDraft.season),
      episode: moveDraft.episode === "" ? null : Number(moveDraft.episode),
      replace_existing_quality: Boolean(moveDraft.replace_existing_quality),
      remove_empty_title: Boolean(moveDraft.remove_empty_title),
      source,
    });
    setModal(null);
    setMoveDraft(null);
    toast(res.moved ? `Moved to ${res.title}` : res.warning || "Move incomplete",
      res.moved ? "success" : "error");
    if (res.old_title_removed) {
      navigate("/admin/media");
    } else {
      await load(false);
    }
  });

  const openMove = (q) => {
    setDraft(q);
    setMoveDraft({
      media_ref: "",
      kind: "",
      quality: "",
      season: q._season ?? "",
      episode: q._episode ?? "",
      replace_existing_quality: false,
      remove_empty_title: true,
    });
    setModal("moveFile");
  };

  const setMove = (key) => (e) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setMoveDraft((d) => ({ ...d, [key]: value }));
  };

  const saveSub = withBusy(async () => {
    await api.patch(
      `${base}/subtitles/${encodeURIComponent(draft.sub.id)}`,
      { language: draft.language, format: draft.format, name: draft.name },
      { file_id: draft.fileId, ...epParams(draft) }
    );
    setModal(null);
    await load(false);
    toast("Subtitle updated");
  });

  const deleteSub = withBusy(async (telegramToo) => {
    const res = await api.delete(`${base}/subtitles/${encodeURIComponent(draft.sub.id)}`, {
      file_id: draft.fileId,
      ...epParams(draft),
      ...(telegramToo ? { delete_telegram: "true" } : {}),
    });
    setModal(null);
    await load(false);
    toast(telegramToo ? `Subtitle deleted • Telegram ${res.telegram_deleted || 0}/${res.telegram_total || 0}` : "Subtitle removed from DB only");
  });

  const addSub = withBusy(async () => {
    const payload = {
      id: draft.id,
      name: draft.name,
      language: draft.language,
      format: draft.format,
    };
    if (isTv && draft.target === "all") {
      await api.post(`${base}/episodes/subtitles`, { ...payload, season_number: "all" });
    } else {
      const q = qualities.find((x) => (x.id || "") === draft.target);
      payload.target_file_id = draft.target;
      if (hasSeason(q)) {
        payload.season_number = q._season;
        payload.episode_number = q._episode;
      }
      await api.post(`${base}/subtitles`, payload);
    }
    setModal(null);
    await load(false);
    toast("Subtitle attached");
  });

  const saveEpisode = withBusy(async () => {
    await api.patch(`${base}/episodes/${draft.season_number}/${draft.episode_number}`, {
      title: draft.title,
      episode_backdrop: draft.episode_backdrop,
    });
    setModal(null);
    await load(false);
    toast("Episode updated");
  });

  const addEpisode = withBusy(async () => {
    await api.post(`${base}/episodes`, {
      season_number: draft.season_number,
      episode_number: draft.episode_number,
      title: draft.title,
    });
    setModal(null);
    await load(false);
    toast("Episode added");
  });

  const deleteEpisode = withBusy(async (telegramToo) => {
    const res = await api.delete(`${base}/episodes/${draft.season_number}/${draft.episode_number}`, {
      ...(telegramToo ? { delete_telegram: "true" } : {}),
    });
    setModal(null);
    await load(false);
    toast(
      telegramToo
        ? `Episode deleted • Telegram ${res.telegram_deleted || 0}/${res.telegram_total || 0}`
        : "Episode removed from DB only"
    );
  });

  const deleteSeason = withBusy(async (telegramToo) => {
    const res = await api.delete(`${base}/seasons/${draft.season_number}`, {
      ...(telegramToo ? { delete_telegram: "true" } : {}),
    });
    setModal(null);
    await load(false);
    toast(
      telegramToo
        ? `Season deleted • ${res.episodes || 0} episodes • Telegram ${res.telegram_deleted || 0}/${res.telegram_total || 0}`
        : `Season removed from DB only • ${res.episodes || 0} episodes`
    );
  });

  const deleteMedia = withBusy(async (telegramToo) => {
    const res = await api.delete(base, telegramToo ? { delete_telegram: "true" } : undefined);
    toast(
      telegramToo
        ? `Media deleted • Telegram ${res.telegram_deleted || 0}/${res.telegram_total || 0}`
        : "Media deleted from the database only"
    );
    navigate("/admin/media");
  });

  if (loading)
    return (
      <div className="flex justify-center py-24">
        <Spinner className="w-6 h-6" />
      </div>
    );
  if (!media)
    return (
      <Panel>
        <EmptyState title="Title not found">It may have been deleted from another tab.</EmptyState>
      </Panel>
    );

  const subCount = qualities.reduce((n, q) => n + (q.subtitle_tracks || []).length, 0);
  const epCount = (media.seasons || []).reduce((n, s) => n + (s.episodes || []).length, 0);
  const sitePath = `${isTv ? "ser" : "mov"}/${media.tmdb_id}`;

  return (
    <div className="space-y-5">
      <Link
        to="/admin/media"
        className="inline-flex items-center gap-2 text-sm text-bone/50 hover:text-bone transition-colors"
      >
        <FiArrowLeft size={14} /> Back to library
      </Link>

      {/* Hero */}
      <section className="relative overflow-hidden rounded-lg border border-bone/8">
        <div className="absolute inset-0">
          <img src={form.backdrop || form.poster} alt="" className="w-full h-full object-cover opacity-35" />
          <div className="absolute inset-0 bg-gradient-to-t from-ink via-ink/85 to-ink/40" />
        </div>
        <div className="relative flex flex-col sm:flex-row gap-4 p-4 sm:p-6">
          <img
            src={form.poster || form.backdrop}
            alt=""
            className="w-24 sm:w-28 aspect-[2/3] object-cover rounded-md ring-1 ring-inset ring-bone/15 shrink-0 bg-carbon"
          />
          <div className="min-w-0 flex-1">
            <p className="timecode text-[10px] uppercase tracking-[0.26em] text-projector/80">
              Editing · {media.type}
            </p>
            <h1 className="font-display text-2xl sm:text-3xl mt-1.5 leading-tight">
              {media.title || "Untitled"}
            </h1>
            <p className="text-sm text-bone/50 mt-1.5 line-clamp-2 max-w-2xl">{media.description}</p>
            <div className="flex flex-wrap items-center gap-2 mt-3.5">
              <Badge tone="neutral">{media.file_count || qualities.length} files</Badge>
              <Badge tone="neutral">{subCount} subs</Badge>
              {isTv && <Badge tone="neutral">{epCount} episodes</Badge>}
              <Badge>db {media.db_index}</Badge>
              <Badge>tmdb {media.tmdb_id}</Badge>
              <a
                href={`${window.location.origin}/${sitePath}`}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 timecode text-[10px] uppercase tracking-[0.14em] text-bone/50 hover:text-projector transition-colors ml-1"
              >
                <FiExternalLink size={12} /> Live page
              </a>
              {media.telegram_link && (
                <a
                  href={media.telegram_link}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 timecode text-[10px] uppercase tracking-[0.14em] text-bone/50 hover:text-projector transition-colors"
                >
                  <FiExternalLink size={12} /> Telegram post
                </a>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* Tabs */}
      <div className="flex gap-1 flex-wrap border-b border-bone/8 pb-px">
        {TABS.filter((t) => !t.tvOnly || isTv).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cx(
              "relative px-4 py-2.5 text-sm transition-colors rounded-t-md",
              tab === t.id
                ? "text-bone font-semibold"
                : "text-bone/50 hover:text-bone/80",
              t.id === "danger" && tab !== t.id && "text-signal/60 hover:text-signal"
            )}
          >
            {t.label}
            {tab === t.id && (
              <span className="absolute left-2 right-2 -bottom-px h-[2px] rounded-full bg-projector" />
            )}
          </button>
        ))}
      </div>

      {tab === "details" && (
        <Panel
          title="Details"
          subtitle="Saved straight to MongoDB"
          actions={
            <>
              <Button variant="ghost" disabled={busy} onClick={() => refreshTmdb(false)}>
                <FiRefreshCw size={14} /> Pull from TMDB
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => refreshTmdb(true)}>
                Overwrite with TMDB
              </Button>
              <Button variant="primary" disabled={busy} onClick={saveDetails}>
                {busy ? <Spinner /> : <FiSave size={14} />} Save details
              </Button>
            </>
          }
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Label htmlFor="ed-title">Title</Label>
              <TextInput id="ed-title" value={form.title} onChange={set("title")} />
            </div>
            <Field label="Release year">
              <TextInput type="number" value={form.release_year} onChange={set("release_year")} />
            </Field>
            <Field label="Rating">
              <TextInput value={form.rating} onChange={set("rating")} placeholder="7.8" />
            </Field>
            <Field
              label={isTv ? "Seasons / episodes" : "Runtime"}
              hint={isTv ? "Read-only — recalculated when you edit episodes." : undefined}
            >
              <TextInput value={form.runtime} onChange={set("runtime")} readOnly={isTv} className={isTv ? "opacity-60" : ""} />
            </Field>
            <Field label="Rip tag">
              <TextInput value={form.rip} onChange={set("rip")} placeholder="WEB-DL • x264" />
            </Field>
            <Field label="Languages" hint="Comma separated">
              <TextInput value={form.languages} onChange={set("languages")} placeholder="Tamil, Telugu, Hindi" />
            </Field>
            <Field label="Genres" hint="Comma separated">
              <TextInput value={form.genres} onChange={set("genres")} placeholder="Action, Drama" />
            </Field>
            <div className="sm:col-span-2">
              <Label htmlFor="ed-desc">Description</Label>
              <TextArea id="ed-desc" value={form.description} onChange={set("description")} rows={5} />
            </div>
          </div>
        </Panel>
      )}

      {tab === "files" && (
        <Panel title="Video files" subtitle={`${qualities.length} qualities stored in Telegram`}>
          {qualities.length === 0 ? (
            <EmptyState title="No video files yet">
              Uploads still go through the Telegram bot — this list shows what was indexed.
            </EmptyState>
          ) : (
            <ul className="space-y-2">
              {qualities.map((q, i) => (
                <li
                  key={q.id || i}
                  className="flex items-center gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-3 hover:border-bone/18 transition-colors"
                >
                  <span className="w-8 h-8 rounded-md bg-bone/5 flex items-center justify-center text-bone/50 shrink-0">
                    🎞
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-bone truncate">{q.name || "Untitled file"}</p>
                    <p className="timecode text-[10px] text-bone/60 mt-0.5 uppercase tracking-wider">
                      {q.quality || "—"} • {q.size || "?"}
                      {q.abyss ? " • abyss" : ""}
                      {q._season ? ` • S${q._season}E${q._episode}` : ""} • {(q.subtitle_tracks || []).length} subs
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    disabled={busy}
                    onClick={() => {
                      setDraft(q);
                      setModal("editFile");
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={busy}
                    title="Move this file to the correct title / episode"
                    onClick={() => openMove(q)}
                  >
                    <FiShuffle size={14} /> Move
                  </Button>
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={() => {
                      setDraft(q);
                      setModal("deleteFile");
                    }}
                  >
                    <FiTrash2 size={14} />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      {tab === "subs" && (
        <Panel
          title="Subtitles"
          subtitle={`${subCount} tracks across all qualities`}
          actions={
            <Button
              variant="primary"
              disabled={busy || qualities.length === 0}
              onClick={() => {
                setDraft({ target: "all", id: "", name: "", language: "Tamil", format: "srt" });
                setModal("addSub");
              }}
            >
              Attach subtitle
            </Button>
          }
        >
          {subCount === 0 ? (
            <EmptyState title="No subtitles attached">Use “Attach subtitle” with a Telegram file id.</EmptyState>
          ) : (
            <ul className="space-y-2">
              {qualities.flatMap((q) =>
                (q.subtitle_tracks || []).map((s, i) => (
                  <li
                    key={`${q.id}-${s.id}-${i}`}
                    className="flex items-center gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-3 hover:border-bone/18 transition-colors"
                  >
                    <span className="w-8 h-8 rounded-md bg-bone/5 flex items-center justify-center text-bone/50 shrink-0">
                      💬
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-bone truncate">{s.name || s.label || "Subtitle"}</p>
                      <p className="timecode text-[10px] text-bone/60 mt-0.5 uppercase tracking-wider">
                        {s.language || "Unknown"} • {(s.format || "srt").toUpperCase()} • on {q.quality || "file"}
                        {q._season ? ` S${q._season}E${q._episode}` : ""}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      disabled={busy}
                      onClick={() => {
                        setDraft({ ...q, sub: s, fileId: q.id, language: s.language, format: s.format, name: s.name });
                        setModal("editSub");
                      }}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="danger"
                      disabled={busy}
                      onClick={() => {
                        setDraft({ ...q, sub: s, fileId: q.id });
                        setModal("deleteSub");
                      }}
                    >
                      <FiTrash2 size={14} />
                    </Button>
                  </li>
                ))
              )}
            </ul>
          )}
        </Panel>
      )}

      {tab === "episodes" && isTv && (
        <Panel
          title="Episodes"
          subtitle={`${(media.seasons || []).length} seasons • ${epCount} episodes`}
          actions={
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => {
                setDraft({ season_number: 1, episode_number: 1, title: "" });
                setModal("addEpisode");
              }}
            >
              Add episode
            </Button>
          }
        >
          {(media.seasons || []).length === 0 ? (
            <EmptyState title="No seasons yet">Add the first episode to create season 1.</EmptyState>
          ) : (
            <div className="space-y-5">
              {(media.seasons || []).map((s) => (
                <div key={s.season_number}>
                  <div className="flex items-center gap-2 mb-2">
                    <h3 className="section-title text-base text-bone">Season {s.season_number}</h3>
                    <span className="timecode text-[10px] text-bone/55 uppercase tracking-[0.14em]">
                      {(s.episodes || []).length} eps
                    </span>
                    <Button
                      variant="danger"
                      className="ml-auto"
                      disabled={busy}
                      onClick={() => {
                        setDraft({
                          season_number: s.season_number,
                          episodes: (s.episodes || []).length,
                        });
                        setModal("deleteSeason");
                      }}
                    >
                      <FiTrash2 size={14} /> Delete season
                    </Button>
                  </div>
                  <ul className="space-y-1.5">
                    {(s.episodes || []).map((ep) => (
                      <li
                        key={ep.episode_number}
                        className="flex items-center gap-3 rounded-md border border-bone/8 bg-ink/40 px-3.5 py-2.5"
                      >
                        <span className="timecode text-[11px] text-projector/80 shrink-0">
                          E{String(ep.episode_number).padStart(2, "0")}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm text-bone truncate">{ep.title || "Untitled episode"}</p>
                          <p className="timecode text-[10px] text-bone/55 mt-0.5">
                            {(ep.telegram || []).length} files •{" "}
                            {(ep.telegram || []).reduce((n, q) => n + (q.subtitle_tracks || []).length, 0)} subs
                          </p>
                        </div>
                        <Button
                          variant="ghost"
                          disabled={busy}
                          onClick={() => {
                            setDraft({
                              season_number: s.season_number,
                              episode_number: ep.episode_number,
                              title: ep.title || "",
                              episode_backdrop: ep.episode_backdrop || "",
                            });
                            setModal("editEpisode");
                          }}
                        >
                          Edit
                        </Button>
                        <Button
                          variant="danger"
                          disabled={busy}
                          onClick={() => {
                            setDraft({ season_number: s.season_number, episode_number: ep.episode_number, title: ep.title });
                            setModal("deleteEpisode");
                          }}
                        >
                          <FiTrash2 size={14} />
                        </Button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}

      {tab === "images" && (
        <Panel
          title="Images"
          actions={
            <Button variant="primary" disabled={busy} onClick={saveImages}>
              <FiSave size={14} /> Save images
            </Button>
          }
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="ed-poster">Poster URL</Label>
              <TextInput id="ed-poster" value={form.poster} onChange={set("poster")} />
              <img
                src={form.poster}
                alt="Poster preview"
                className="mt-3 w-28 aspect-[2/3] object-cover rounded-md ring-1 ring-inset ring-bone/12 bg-carbon"
              />
            </div>
            <div>
              <Label htmlFor="ed-backdrop">Backdrop URL</Label>
              <TextInput id="ed-backdrop" value={form.backdrop} onChange={set("backdrop")} />
              <img
                src={form.backdrop}
                alt="Backdrop preview"
                className="mt-3 w-full aspect-video object-cover rounded-md ring-1 ring-inset ring-bone/12 bg-carbon"
              />
            </div>
          </div>
        </Panel>
      )}

      {tab === "cast" && (
        <Panel
          title="Cast"
          subtitle="One per line — Name | Role | Image URL | TMDB id"
          actions={
            <Button variant="primary" disabled={busy} onClick={saveDetails}>
              <FiSave size={14} /> Save cast
            </Button>
          }
        >
          <TextArea value={form.cast} onChange={set("cast")} rows={14} className="font-mono text-[13px]" />
        </Panel>
      )}

      {tab === "danger" && (
        <Panel title="Danger zone" subtitle="Irreversible — Telegram deletion is permanent">
          <div className="rounded-lg border border-signal/30 bg-signal/6 p-4 sm:p-5 space-y-4">
            <p className="text-sm text-bone/70 leading-relaxed">
              Deleting removes <strong className="text-bone">{media.title}</strong> from the catalogue.{" "}
              <span className="text-bone/50">DB only</span> leaves the files on your Telegram channel;{" "}
              <span className="text-bone/50">Telegram + DB</span> also deletes every video message and its
              subtitles.
            </p>
            <div className="max-w-xs">
              <Label htmlFor="danger-word">Type DELETE to confirm</Label>
              <TextInput
                id="danger-word"
                value={deleteWord}
                onChange={(e) => setDeleteWord(e.target.value)}
                placeholder="DELETE"
                autoComplete="off"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="danger"
                disabled={busy || deleteWord.trim().toUpperCase() !== "DELETE"}
                onClick={() => {
                  setDraft({ telegram: false });
                  setModal("deleteMedia");
                }}
              >
                <FiCheck size={14} /> Delete from DB only
              </Button>
              <Button
                variant="danger"
                disabled={busy || deleteWord.trim().toUpperCase() !== "DELETE"}
                onClick={() => {
                  setDraft({ telegram: true });
                  setModal("deleteMedia");
                }}
              >
                <FiTrash2 size={14} /> Delete Telegram + DB
              </Button>
            </div>
          </div>
        </Panel>
      )}

      {/* ── Modals ── */}
      <Modal open={modal === "editFile"} onClose={() => setModal(null)} title="Edit video file">
        {draft && (
          <div className="space-y-4">
            <p className="timecode text-[10px] text-bone/60 break-all">{draft.id}</p>
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Quality">
                <TextInput
                  value={draft.quality || ""}
                  onChange={(e) => setDraft({ ...draft, quality: e.target.value })}
                />
              </Field>
              <Field label="Size">
                <TextInput value={draft.size || ""} onChange={(e) => setDraft({ ...draft, size: e.target.value })} />
              </Field>
            </div>
            <Field label="File name">
              <TextArea value={draft.name || ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={busy} onClick={saveFile}>
                <FiSave size={14} /> Save file
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "deleteFile"} onClose={() => setModal(null)} title="Delete video file">
        {draft && (
          <div className="space-y-4">
            <p className="timecode text-[11px] text-bone/60 break-all">{draft.name}</p>
            <p className="text-sm text-bone/50 leading-relaxed">
              DB only keeps the Telegram channel post. Telegram + DB deletes the video message and its linked
              subtitle messages, then removes the quality from the document.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteFile(false)}>
                DB only
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteFile(true)}>
                Telegram + DB
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "moveFile"} onClose={() => setModal(null)} title="Move file to another title">
        {draft && moveDraft && (
          <div className="space-y-4">
            <p className="text-[11px] text-bone/50 leading-relaxed break-all">
              Moving <span className="text-bone/80">{draft.name}</span> ({draft.quality || "—"}).
              The Telegram post and the stream link are untouched; only the library entry moves.
            </p>
            <Field
              label="Correct TMDB / IMDb reference"
              hint="tt0133093, an IMDb link, a TMDB id, or a themoviedb.org/movie|tv link"
            >
              <TextInput
                value={moveDraft.media_ref}
                onChange={setMove("media_ref")}
                placeholder="603 · imdb.com/title/tt0133093 · themoviedb.org/tv/1396"
                autoCapitalize="none"
                spellCheck={false}
                className="font-mono text-[12px]"
              />
            </Field>
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label="Kind">
                <Select value={moveDraft.kind} onChange={setMove("kind")}>
                  <option value="">Auto</option>
                  <option value="movie">Movie</option>
                  <option value="tv">Series</option>
                </Select>
              </Field>
              <Field label="Quality (optional — blank keeps it)">
                <TextInput
                  value={moveDraft.quality}
                  onChange={setMove("quality")}
                  placeholder={draft.quality || "keep"}
                />
              </Field>
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              <Field label="Season (TV)">
                <TextInput
                  type="number"
                  min="1"
                  value={moveDraft.season}
                  onChange={setMove("season")}
                  placeholder="1"
                />
              </Field>
              <Field label="Episode (TV)">
                <TextInput
                  type="number"
                  min="1"
                  value={moveDraft.episode}
                  onChange={setMove("episode")}
                  placeholder="1"
                />
              </Field>
            </div>
            <div className="space-y-2">
              <label className="flex items-start gap-2.5 text-[12px] text-bone/65 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-[#e9b872]"
                  checked={moveDraft.replace_existing_quality}
                  onChange={setMove("replace_existing_quality")}
                />
                <span>Replace a same-quality file already on the target</span>
              </label>
              <label className="flex items-start gap-2.5 text-[12px] text-bone/65 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5 accent-[#e9b872]"
                  checked={moveDraft.remove_empty_title}
                  onChange={setMove("remove_empty_title")}
                />
                <span>Delete this title if it has no files left</span>
              </label>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setModal(null)}>Cancel</Button>
              <Button variant="primary" disabled={busy} onClick={moveFile}>
                <FiShuffle size={14} /> Move file
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "addSub"} onClose={() => setModal(null)} title="Attach subtitle">
        {draft && (
          <div className="space-y-4">
            <Field label="Target file">
              <Select value={draft.target} onChange={(e) => setDraft({ ...draft, target: e.target.value })}>
                <option value="all">All qualities{isTv ? " (all episodes)" : ""}</option>
                {qualities.map((q, i) => (
                  <option key={q.id || i} value={q.id}>
                    {q.quality} • {String(q.name || "").slice(0, 40)}
                    {q._season ? ` (S${q._season}E${q._episode})` : ""}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Encoded subtitle id" hint="Telegram file id as stored by the bot.">
              <TextInput
                value={draft.id}
                onChange={(e) => setDraft({ ...draft, id: e.target.value })}
                placeholder="AgADBa4x…"
              />
            </Field>
            <Field label="File name">
              <TextInput
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="movie_tamil.srt"
              />
            </Field>
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Language">
                <TextInput value={draft.language} onChange={(e) => setDraft({ ...draft, language: e.target.value })} />
              </Field>
              <Field label="Format">
                <TextInput value={draft.format} onChange={(e) => setDraft({ ...draft, format: e.target.value })} />
              </Field>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={busy || !draft.id} onClick={addSub}>
                <FiCheck size={14} /> Attach
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "editSub"} onClose={() => setModal(null)} title="Edit subtitle">
        {draft && (
          <div className="space-y-4">
            <p className="timecode text-[10px] text-bone/60 break-all">{draft.sub?.id}</p>
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Language">
                <TextInput value={draft.language || ""} onChange={(e) => setDraft({ ...draft, language: e.target.value })} />
              </Field>
              <Field label="Format">
                <TextInput value={draft.format || ""} onChange={(e) => setDraft({ ...draft, format: e.target.value })} />
              </Field>
            </div>
            <Field label="File name">
              <TextArea value={draft.name || ""} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={busy} onClick={saveSub}>
                <FiSave size={14} /> Save subtitle
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "deleteSub"} onClose={() => setModal(null)} title="Delete subtitle">
        {draft && (
          <div className="space-y-4">
            <p className="timecode text-[11px] text-bone/60 break-all">{draft.sub?.name}</p>
            <p className="text-sm text-bone/50 leading-relaxed">
              DB only unlinks the track. Telegram + DB also deletes the original subtitle message from the channel.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteSub(false)}>
                DB only
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteSub(true)}>
                Telegram + DB
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "addEpisode"} onClose={() => setModal(null)} title="Add episode">
        {draft && (
          <div className="space-y-4">
            <div className="grid sm:grid-cols-2 gap-4">
              <Field label="Season">
                <TextInput
                  type="number"
                  value={draft.season_number}
                  onChange={(e) => setDraft({ ...draft, season_number: e.target.value })}
                />
              </Field>
              <Field label="Episode">
                <TextInput
                  type="number"
                  value={draft.episode_number}
                  onChange={(e) => setDraft({ ...draft, episode_number: e.target.value })}
                />
              </Field>
            </div>
            <Field label="Title" hint="Leave blank to use “Episode N”.">
              <TextInput value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={busy} onClick={addEpisode}>
                <FiCheck size={14} /> Add episode
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "editEpisode"} onClose={() => setModal(null)} title="Edit episode">
        {draft && (
          <div className="space-y-4">
            <p className="timecode text-[10px] text-bone/60">
              S{draft.season_number} • E{draft.episode_number}
            </p>
            <Field label="Title">
              <TextInput value={draft.title || ""} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            </Field>
            <Field label="Episode backdrop URL">
              <TextInput
                value={draft.episode_backdrop || ""}
                onChange={(e) => setDraft({ ...draft, episode_backdrop: e.target.value })}
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={busy} onClick={saveEpisode}>
                <FiSave size={14} /> Save episode
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "deleteEpisode"} onClose={() => setModal(null)} title="Delete episode">
        {draft && (
          <div className="space-y-4">
            <p className="text-sm text-bone/70">
              S{draft.season_number} • E{draft.episode_number} — {draft.title || "Untitled"}
            </p>
            <p className="text-sm text-bone/50 leading-relaxed">
              The episode is removed and empty seasons are pruned afterwards. DB only leaves its video and
              subtitle messages on the Telegram channel; Telegram + DB deletes them too.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteEpisode(false)}>
                DB only
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteEpisode(true)}>
                Telegram + DB
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={modal === "deleteSeason"} onClose={() => setModal(null)} title="Delete whole season">
        {draft && (
          <div className="space-y-4">
            <p className="text-sm text-bone/70">
              Season {draft.season_number} — {draft.episodes} episode(s)
            </p>
            <p className="text-sm text-bone/50 leading-relaxed">
              Every episode and quality file in this season is removed from the document. DB only keeps the
              Telegram posts (they become orphans and still use cluster quota); Telegram + DB deletes each
              episode&rsquo;s video and subtitle messages first.
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setModal(null)}>
                Cancel
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteSeason(false)}>
                DB only
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => deleteSeason(true)}>
                Telegram + DB
              </Button>
            </div>
          </div>
        )}
      </Modal>

      <ConfirmModal
        open={modal === "deleteMedia"}
        onClose={() => setModal(null)}
        busy={busy}
        title={draft?.telegram ? "Delete from Telegram and the database" : "Delete from the database"}
        confirmWord="DELETE"
        confirmLabel={draft?.telegram ? "Delete everything" : "Delete from DB"}
        onConfirm={() => deleteMedia(!!draft?.telegram)}
      />
    </div>
  );
}
