import axios from "axios";
import React, { useState, useEffect } from "react";
import { Select, SelectItem } from "@nextui-org/select";
import { Popover, PopoverTrigger, PopoverContent } from "@nextui-org/popover";
import { Button } from "@nextui-org/button";
import { FaCloudDownloadAlt, FaPlay } from "react-icons/fa";
import Spinner from "./svg/Spinner";

const DownloadButton = ({ movieData, btnType }) => {
  const BASE = import.meta.env.VITE_BASE_URL;
  const API_URL = import.meta.env.VITE_API_URL;
  const API_KEY = import.meta.env.VITE_API_KEY;

  const [selectedSeason, setSelectedSeason] = useState("");
  const [selectedEpisode, setSelectedEpisode] = useState("");
  const [selectedQuality, setSelectedQuality] = useState("");
  const [episodes, setEpisodes] = useState([]);
  const [qualities, setQualities] = useState([]);
  const [loading, setLoading] = useState({});

  useEffect(() => {
    if (selectedSeason) {
      const season = movieData.seasons.find(
        (s) => s.season_number === parseInt(selectedSeason)
      );
      if (season) {
        setEpisodes(season.episodes);
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
        setQualities(episode.telegram);
        setSelectedQuality("");
      }
    }
  }, [selectedEpisode, episodes]);

  const shortenUrl = async (url) => {
  try {
    // Flexible structure for various APIs
    const response = await axios.get(API_URL, {
    params: {
      api: API_KEY,   // ✅ Correct key name
      url: url,       // ✅ Correct param for URL
      format: "json", // ✅ Optional but good to include for clarity
    },
  });

    const data = response.data;

    // Adjust this based on expected field in your API response
    return data?.shortenedUrl || data?.short || data?.url || url;
  } catch (error) {
    console.error("Error shortening URL:", error);
    return url;
  }
};


  const generateUrl = (id, name) => {
    const downloadUrl = `${BASE}/dl/${id}/${encodeURIComponent(name)}`;
    if (btnType === "Download") return downloadUrl;
    return `intent:${downloadUrl}#Intent;type=video/x-matroska;action=android.intent.action.VIEW;end;`;
  };

  const handleButtonClick = async (id, name, quality) => {
    setLoading((prev) => ({ ...prev, [quality]: true }));
    const rawUrl = generateUrl(id, name);
    const shortUrl = await shortenUrl(rawUrl);
    setLoading((prev) => ({ ...prev, [quality]: false }));
    window.open(shortUrl, "_blank", "noopener noreferrer");
  };

  const renderMovieButtons = () =>
    movieData.telegram?.map((q, i) => (
      <Button
        key={i}
        onClick={() => handleButtonClick(q.id, q.name, q.quality)}
        size="sm"
        className="bg-primaryBtn rounded-full"
        isLoading={loading[q.quality]}
        spinner={<Spinner />}
      >
        {q.quality}
      </Button>
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
        {movieData.seasons
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
        {qualities?.map((q) => (
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
        {btnType === "Download" ? "Download" : "Open in Player"}
      </Button>
    </div>
  );

  return (
    <Popover placement="bottom" showArrow={true}>
      <PopoverTrigger>
        <button className="flex justify-center items-center gap-2 uppercase text-otherColor max-w-full grow text-xs rounded-full border-2 border-otherColor py-1 px-3 lg:text-sm sm:px-5 sm:max-w-[15rem] sm:py-2">
          {btnType === "Download" ? (
            <>
              <FaCloudDownloadAlt className="text-lg" /> Download
            </>
          ) : (
            <>
              <FaPlay className="text-lg" /> Player
            </>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent className="bg-btnColor">
        {movieData.media_type === "movie"
          ? <div className="px-1 py-2 flex gap-1 flex-wrap">{renderMovieButtons()}</div>
          : renderShowSelectors()}
      </PopoverContent>
    </Popover>
  );
};

export default DownloadButton;
