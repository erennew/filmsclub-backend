import React from "react";
import { Link } from "react-router-dom";
import { LazyLoadImage } from "react-lazy-load-image-component";
import "react-lazy-load-image-component/src/effects/black-and-white.css";
import { BsPlayFill } from "react-icons/bs";
import posterPlaceholder from "../assets/images/poster-placeholder.png";

const TopTenToday = ({ data, isLoading, sectionTitle }) => {
  if (isLoading) {
    return (
      <div className="mt-[2.5rem] md:mt-[5rem]">
        <div className="pl-[1rem] border-l-2 border-primaryBtn mb-[1.5rem]">
          <p className="text-[0.8rem] uppercase font-bold sm:text-[1rem] text-primaryTextColor">
            {sectionTitle}
          </p>
        </div>
        <div className="flex gap-4 overflow-x-auto pb-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="flex-shrink-0 w-[140px] sm:w-[180px] aspect-[9/13.5] rounded-2xl bg-bgColorSecondary animate-pulse"
            />
          ))}
        </div>
      </div>
    );
  }

  if (!data || data.length === 0) return null;

  return (
    <div className="mt-[2.5rem] md:mt-[5rem]">
      {/* Title */}
      <div className="flex items-center flex-wrap gap-5 text-primaryTextColor pb-[1.5rem]">
        <div className="pl-[1rem] border-l-2 border-primaryBtn">
          <p className="text-[0.8rem] uppercase font-bold sm:text-[1rem]">
            {sectionTitle}
          </p>
        </div>
      </div>

      {/* Horizontal numbered carousel */}
      <div className="flex gap-3 sm:gap-4 overflow-x-auto pb-4 snap-x snap-mandatory">
        {data.map((item, index) => (
          <Link
            key={item.tmdb_id}
            to={
              item.media_type === "movie"
                ? `/mov/${item.tmdb_id}`
                : `/ser/${item.tmdb_id}`
            }
            className="flex-shrink-0 w-[140px] sm:w-[180px] relative group snap-start"
          >
            {/* Number badge */}
            <div className="absolute -top-2 -left-2 z-10 flex items-center justify-center w-8 h-8 sm:w-10 sm:h-10 rounded-full bg-primaryBtn text-bgColor font-extrabold text-sm sm:text-base shadow-lg">
              {index + 1}
            </div>

            {/* Poster */}
            <div className="relative aspect-[9/13.5] w-full overflow-hidden rounded-2xl">
              <LazyLoadImage
                src={item.poster ? item.poster : posterPlaceholder}
                effect="black-and-white"
                alt={item.title}
                className="aspect-[9/13.5] w-full object-cover rounded-2xl transition-transform duration-300 group-hover:scale-105"
              />

              {/* Hover overlay */}
              <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity duration-300 flex items-center justify-center rounded-2xl">
                <div className="text-3xl p-1 rounded-full border-4 border-primaryBtn text-primaryBtn">
                  <BsPlayFill />
                </div>
              </div>

              {/* View count badge */}
              {item.view_count && (
                <div className="absolute top-2 right-2 bg-primaryBtn text-bgColor py-1 px-2 rounded-full text-[0.55rem] sm:text-[0.65rem] font-bold">
                  {item.view_count} views
                </div>
              )}
            </div>

            {/* Title */}
            <div className="mt-2 text-primaryTextColor">
              <p className="line-clamp-1 text-sm font-medium">{item.title}</p>
              <div className="flex items-center justify-between text-secondaryTextColor mt-1 text-[0.6rem] sm:text-xs">
                {item.release_year && <span>{item.release_year}</span>}
                <span className="uppercase bg-bgColorSecondary text-primaryTextColor py-0.5 px-2 rounded-full">
                  {item.media_type}
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
};

export default TopTenToday;
