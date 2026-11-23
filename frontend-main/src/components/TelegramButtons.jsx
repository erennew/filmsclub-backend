import React, { useState } from "react";
import { Popover, PopoverTrigger, PopoverContent } from "@nextui-org/popover";
import { PiTelegramLogo } from "react-icons/pi";
import { Button } from "@nextui-org/button";
import Spinner from "./svg/Spinner";
import { TG_USERNAME as USERNAME } from "../config";

const TelegramButton = ({ movieData }) => {

  const [loading, setLoading] = useState({});

  const handleButtonClick = (originalUrl, quality) => {
    setLoading((prev) => ({ ...prev, [quality]: true }));
    window.open(originalUrl, "_blank", "noopener noreferrer");
    setLoading((prev) => ({ ...prev, [quality]: false }));
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
        className="bg-projector text-ink rounded-[3px] font-semibold"
        isLoading={loading[quality]}
        spinner={<Spinner />}
      >
        {quality}
      </Button>
    ));

  const renderSeasonButtons = () => {
    if (!movieData?.seasons?.length) return null;
    return movieData.seasons.map((season, seasonIndex) => {
      const availableQualities = new Set();
      (season.episodes || []).forEach((episode) => {
        episode.sources?.forEach(({ quality }) =>
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
            <button className="text-left bg-projector text-ink py-1.5 px-3 rounded-[3px] text-sm font-semibold">
              Season {season.season_number}
            </button>
          </PopoverTrigger>
          <PopoverContent className="bg-carbon border border-bone/10 rounded-[4px]">
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
                  className="bg-projector text-ink rounded-[3px] font-semibold"
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
  };

  return (
    <Popover placement="bottom" showArrow={true}>
      <PopoverTrigger>
        <button className="flex items-center justify-center gap-2 bg-projector hover:bg-[#F0CA98] transition-colors max-w-full grow text-ink text-xs font-bold rounded-[3px] py-2 px-3 min-h-[44px] lg:text-sm sm:px-5 sm:max-w-[15rem]">
          <PiTelegramLogo className="text-lg" /> Telegram
        </button>
      </PopoverTrigger>
      <PopoverContent className="bg-carbon border border-bone/10 rounded-[4px]">
        <div className="px-1 py-2 flex gap-1 flex-wrap flex-col">
          {movieData.media_type === "movie"
            ? renderQualityButtons(movieData.sources || [])
            : renderSeasonButtons()}
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default TelegramButton;
