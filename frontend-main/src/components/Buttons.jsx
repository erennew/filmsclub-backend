import React, { useState, useEffect } from "react";
import { BASE_URL as BASE } from "../config";
import { Select, SelectItem } from "@nextui-org/select";
import { Popover, PopoverTrigger, PopoverContent } from "@nextui-org/popover";
import { Button } from "@nextui-org/button";
import { FaCloudDownloadAlt, FaPlay, FaDesktop } from "react-icons/fa";
import Spinner from "./svg/Spinner";

const DownloadButton = ({ movieData, btnType }) => {

  const [selectedSeason, setSelectedSeason] = useState("");
  const [selectedEpisode, setSelectedEpisode] = useState("");
  const [selectedQuality, setSelectedQuality] = useState("");
  const [episodes, setEpisodes] = useState([]);
  const [qualities, setQualities] = useState([]);
  const [loading, setLoading] = useState({});

  useEffect(() => {
    if (selectedSeason) {
      const season = (movieData.seasons || []).find(
        (s) => s.season_number === parseInt(selectedSeason)
      );
      if (season) {
        setEpisodes(season.episodes || []);
        setSelectedEpisode("");
        setQualities([]);
      }
    }
  }, [selectedSeason, movieData.seasons]);

  useEffect(() => {
    if (selectedEpisode) {
      const episode = episodes.find(
        (e) => e.episode_number === parseInt(selectedEpisode)
      );
      if (episode) {
        setQualities(episode.sources);
        setSelectedQuality("");
      }
    }
  }, [selectedEpisode, episodes]);

  const generateUrl = (id, name) => {
    const downloadUrl = `${BASE}/dl/${id}/${encodeURIComponent(name)}`;
    if (btnType === "Download") return downloadUrl;
    if (btnType === "PC Player") return downloadUrl;
    return `intent:${downloadUrl}#Intent;type=video/x-matroska;action=android.intent.action.VIEW;end;`;
  };

  const handleButtonClick = (id, name, quality) => {
    const rawUrl = generateUrl(id, name);
    window.open(rawUrl, "_blank", "noopener noreferrer");
  };

  const handleM3UDownload = (id, name) => {
    const isMobile = /Android|iPhone|iPad/i.test(navigator.userAgent);
    const streamUrl = `${BASE}/dl/${id}/${encodeURIComponent(name)}`;
    const safeName = name.replace(/[^a-zA-Z0-9]/g, "_");

    if (isMobile) {
      // Mobile: direct download intent
      const a = document.createElement("a");
      a.href = streamUrl;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }

    // DESKTOP: Always generate M3U file (most reliable — works with VLC, MPC, mpv, etc.)
    // The M3U playlist contains the raw streaming URL so VLC can parse it correctly.
    generateAndDownloadM3U(streamUrl, safeName);

    // Also try the vlc:// protocol handler (opens VLC directly without file download).
    // We pass the raw URL — NOT encoded again — so VLC can open the MRL correctly.
    // This runs alongside the M3U download; one of the two will always work.
    try {
      const vlcDirectUrl = `vlc://${streamUrl}`;
      const a = document.createElement("a");
      a.href = vlcDirectUrl;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (_) {
      // Silently ignore — M3U file already downloaded above
    }
  };

  const generateAndDownloadM3U = (videoUrl, fileName) => {
    try {
      const m3uContent = `#EXTM3U\n#EXTINF:-1,${fileName}\n${videoUrl}`;
      const blob = new Blob([m3uContent], { type: "audio/x-mpegurl" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${fileName}.m3u`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 100);
    } catch (error) {
      console.error("M3U generation failed:", error);
      // Fallback: direct download
      const a = document.createElement("a");
      a.href = videoUrl;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }
  };

  const handleMP4Download = async (id, name, quality) => {
    setLoading((prev) => ({ ...prev, [quality]: true }));
    try {
      const streamUrl = `${BASE}/dl/${id}/${encodeURIComponent(name)}`;
      const headRes = await fetch(streamUrl, { method: "HEAD" });
      const totalBytes = parseInt(headRes.headers.get("content-length") || "0");
      const chunkSize = 5 * 1024 * 1024;
      const chunks = [];
      let start = 0;
      while (start < totalBytes) {
        const end = Math.min(start + chunkSize - 1, totalBytes - 1);
        const res = await fetch(streamUrl, { headers: { Range: `bytes=${start}-${end}` } });
        const chunk = await res.arrayBuffer();
        chunks.push(chunk);
        start = end + 1;
      }
      const totalLength = chunks.reduce((sum, c) => sum + c.byteLength, 0);
      const combined = new Uint8Array(totalLength);
      let offset = 0;
      for (const chunk of chunks) {
        combined.set(new Uint8Array(chunk), offset);
        offset += chunk.byteLength;
      }
      const blob = new Blob([combined.buffer], { type: "video/mp4" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      console.error("MP4 download failed:", error);
      const streamUrl = `${BASE}/dl/${id}/${encodeURIComponent(name)}`;
      const a = document.createElement("a");
      a.href = streamUrl;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }
    setLoading((prev) => ({ ...prev, [quality]: false }));
  };

  // Sort qualities by number of languages (prefer more languages)
  const sortQualitiesByLanguages = (qualities) => {
    if (!qualities) return [];
    return [...qualities].sort((a, b) => {
      const aLanguages = a.languages?.length || 0;
      const bLanguages = b.languages?.length || 0;
      return bLanguages - aLanguages; // Descending order (more languages first)
    });
  };

  // Format file size for display
  const formatFileSize = (bytes) => {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
  };

  // Enhanced quality info component
  const QualityInfo = ({ quality }) => {
    const info = [];
    if (quality.codec) info.push(quality.codec.toUpperCase());
    if (quality.quality) info.push(quality.quality);
    if (quality.audio) info.push(quality.audio);
    if (quality.size) info.push(formatFileSize(quality.size));
    return info.join(' • ');
  };

  const renderMovieButtons = () =>
    sortQualitiesByLanguages(movieData.sources).map((q, i) => (
      <div key={i} className="flex flex-col gap-1">
        <Button
          onClick={() => handleButtonClick(q.id, q.name, q.quality)}
          size="sm"
          className="bg-projector text-ink rounded-[3px] font-semibold"
          isLoading={loading[q.quality]}
          spinner={<Spinner />}
        >
          {q.quality}
        </Button>
        <div className="font-mono text-[10px] text-bone/50 text-center">
          <QualityInfo quality={q} />
        </div>
      </div>
    ));

  const renderPCPlayerButtons = () =>
    sortQualitiesByLanguages(movieData.sources).map((q, i) => (
      <div key={i} className="flex flex-col gap-1 items-center">
        <span className="font-mono text-[10px] text-bone/60">{q.quality}</span>
        <div className="font-mono text-[10px] text-bone/60 text-center max-w-[4rem]">
          <QualityInfo quality={q} />
        </div>
        <div className="flex gap-1">
          <Button
            onClick={() => handleM3UDownload(q.id, q.name)}
            size="sm"
            className="bg-projector text-ink rounded-[3px] font-semibold min-w-[3.5rem]"
          >
            M3U
          </Button>
          <Button
            onClick={() => handleMP4Download(q.id, q.name, q.quality)}
            size="sm"
            className="bg-bone/[0.06] text-bone border border-bone/15 rounded-[3px] min-w-[3.5rem]"
            isLoading={loading[q.quality]}
            spinner={<Spinner />}
          >
            MP4
          </Button>
        </div>
      </div>
    ));

  const renderShowSelectors = () => (
    <div className="px-1 py-2 flex flex-col gap-2">
      <Select
        isRequired
        variant="bordered"
        aria-label="Select season"
        placeholder="Select season"
        className="w-40 mb-2"
        onChange={(e) => setSelectedSeason(e.target.value)}
        value={selectedSeason}
      >
        {(movieData.seasons || [])
          .sort((a, b) => a.season_number - b.season_number)
          .map((s) => (
            <SelectItem key={s.season_number} value={s.season_number}>
              Season {s.season_number}
            </SelectItem>
          ))}
      </Select>
      <Select
        isRequired
        variant="bordered"
        aria-label="Select episode"
        placeholder="Select episode"
        className="w-40 mb-2"
        onChange={(e) => setSelectedEpisode(e.target.value)}
        value={selectedEpisode}
        disabled={!selectedSeason}
      >
        {episodes
          .sort((a, b) => a.episode_number - b.episode_number)
          .map((e) => (
            <SelectItem key={e.episode_number} value={e.episode_number}>
              Episode {e.episode_number}
            </SelectItem>
          ))}
      </Select>
      <Select
        isRequired
        variant="bordered"
        aria-label="Select quality"
        placeholder="Select quality"
        className="w-40 mb-2"
        onChange={(e) => setSelectedQuality(e.target.value)}
        value={selectedQuality}
        disabled={!selectedEpisode}
      >
        {sortQualitiesByLanguages(qualities).map((q) => (
          <SelectItem key={q.quality} value={q.quality}>
            {q.quality}
          </SelectItem>
        ))}
      </Select>
      <Button
        onClick={() => {
          const q = qualities.find((q) => q.quality === selectedQuality);
          if (q) handleButtonClick(q.id, q.name, q.quality);
        }}
        size="sm"
        className="bg-primaryBtn rounded-full"
        disabled={!selectedQuality}
        isLoading={loading[selectedQuality]}
        spinner={<Spinner />}
      >
        {btnType === "Download" ? "Download" : btnType === "PC Player" ? "Open in PC Player" : "Open in Player"}
      </Button>
      {btnType === "PC Player" && selectedQuality && (
        <>
          <Button
            onClick={() => {
              const q = qualities.find((q) => q.quality === selectedQuality);
              if (q) handleM3UDownload(q.id, q.name);
            }}
            size="sm"
            className="bg-bone/[0.06] text-bone border border-bone/15 rounded-[3px]"
          >
            Get M3U
          </Button>
          <Button
            onClick={() => {
              const q = qualities.find((q) => q.quality === selectedQuality);
              if (q) handleMP4Download(q.id, q.name, q.quality);
            }}
            size="sm"
            className="bg-bone/[0.06] text-bone border border-bone/15 rounded-[3px]"
            isLoading={loading[selectedQuality]}
            spinner={<Spinner />}
          >
            Download MP4
          </Button>
        </>
      )}
    </div>
  );

  return (
    <Popover placement="bottom" showArrow={true}>
      <PopoverTrigger>
        <button className="flex justify-center items-center gap-2 text-projector hover:text-[#F0CA98] max-w-full grow text-xs font-bold rounded-[3px] border border-projector/45 hover:border-projector/70 hover:bg-projector/[0.06] transition-colors py-2 px-3 min-h-[44px] lg:text-sm sm:px-5 sm:max-w-[15rem]">
          {btnType === "Download" ? (
            <>
              <FaCloudDownloadAlt className="text-lg" /> Download
            </>
          ) : btnType === "PC Player" ? (
            <>
              <FaDesktop className="text-lg" /> PC Player
            </>
          ) : (
            <>
              <FaPlay className="text-lg" /> Player
            </>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent className="bg-carbon border border-bone/10 rounded-[4px]">
        {movieData.media_type === "movie"
          ? <div className="px-1 py-2 flex gap-2 flex-wrap">{btnType === "PC Player" ? renderPCPlayerButtons() : renderMovieButtons()}</div>
          : renderShowSelectors()}
      </PopoverContent>
    </Popover>
  );
};

export default DownloadButton;
