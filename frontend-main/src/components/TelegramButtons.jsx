import React, { useState } from "react";
import { Popover, PopoverTrigger, PopoverContent } from "@nextui-org/popover";
import { PiTelegramLogo } from "react-icons/pi";
import axios from "axios";
import { Button } from "@nextui-org/button";
import Spinner from "./svg/Spinner";
import { useLocation } from "react-router-dom";

const TelegramButton = ({ movieData }) => {
  const USERNAME = import.meta.env.VITE_TG_USERNAME;
  const API_URL = import.meta.env.VITE_API_URL;
  const API_KEY = import.meta.env.VITE_API_KEY;

  const [shortenedUrls, setShortenedUrls] = useState({});
  const [loading, setLoading] = useState({});
  const location = useLocation();

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


  const handleButtonClick = async (originalUrl, quality) => {
    setLoading((prev) => ({ ...prev, [quality]: true }));
    let shortUrl = originalUrl;

    try {
      shortUrl = await shortenUrl(originalUrl);
      setShortenedUrls((prev) => ({ ...prev, [originalUrl]: shortUrl }));
    } catch (error) {
      console.error("Error processing URL:", error);
    } finally {
      setLoading((prev) => ({ ...prev, [quality]: false }));
      window.open(shortUrl, "_blank", "noopener noreferrer");
    }
  };

  const renderQualityButtons = (qualityDetails) =>
    qualityDetails.map(({ quality }, index) => (
      <Button
        key={index}
        onClick={() =>
          handleButtonClick(
            `https://t.me/${USERNAME}?start=file_${movieData.tmdb_id}_${quality}`,
            quality
          )
        }
        size="sm"
        className="bg-primaryBtn rounded-full"
        isLoading={loading[quality]}
        spinner={<Spinner />}
      >
        {quality}
      </Button>
    ));

  const renderSeasonButtons = () =>
    movieData.seasons.map((season, seasonIndex) => {
      const availableQualities = new Set();
      season.episodes.forEach((episode) => {
        episode.telegram?.forEach(({ quality }) =>
          availableQualities.add(quality)
        );
      });

      return (
        <Popover
          key={seasonIndex}
          placement="left"
          showArrow={true}
          offset={20}
        >
          <PopoverTrigger>
            <button className="text-left bg-otherColor text-bgColor py-1 px-3 rounded-full border-2 border-otherColor">
              Season {season.season_number}
            </button>
          </PopoverTrigger>
          <PopoverContent className="bg-btnColor">
            <div className="px-1 py-2 flex gap-1 flex-wrap">
              {Array.from(availableQualities).map((quality, qualityIndex) => (
                <Button
                  key={qualityIndex}
                  onClick={() =>
                    handleButtonClick(
                      `https://t.me/${USERNAME}?start=file_${movieData.tmdb_id}_${season.season_number}_${quality}`,
                      quality
                    )
                  }
                  size="sm"
                  className="bg-primaryBtn rounded-full"
                  isLoading={loading[quality]}
                  spinner={<Spinner />}
                >
                  {quality}
                </Button>
              ))}
            </div>
          </PopoverContent>
        </Popover>
      );
    });

  return (
    <Popover placement="bottom" showArrow={true}>
      <PopoverTrigger>
        <button className="uppercase flex items-center justify-center gap-2 bg-otherColor max-w-full grow text-bgColor text-xs rounded-full border-2 border-otherColor py-1 px-3 lg:text-sm sm:px-5 sm:max-w-[15rem] sm:py-2">
          <PiTelegramLogo className="text-lg" /> Telegram
        </button>
      </PopoverTrigger>
      <PopoverContent className="bg-btnColor">
        <div className="px-1 py-2 flex gap-1 flex-wrap flex-col">
          {movieData.media_type === "movie"
            ? renderQualityButtons(movieData.telegram || [])
            : renderSeasonButtons()}
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default TelegramButton;
