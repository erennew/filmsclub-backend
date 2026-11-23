import React from "react";
import { Link } from "react-router-dom";
import { LazyLoadImage } from "react-lazy-load-image-component";
import "react-lazy-load-image-component/src/effects/blur.css";
import { PiStarFill } from "react-icons/pi";
import { BsPlayFill } from "react-icons/bs";
import posterPlaceholder from "../assets/images/poster-placeholder.png";
import { posterUrl } from "../utils/imageUrl";
import WatchlistButton from "./WatchlistButton";

const MovieCard = ({ movie }) => {
  const link =
    movie.media_type === "movie"
      ? `/mov/${movie.tmdb_id}`
      : `/ser/${movie.tmdb_id}`;

  const kind = movie.media_type === "tv" ? "Series" : "Movie";
  const rating =
    movie.rating != null
      ? typeof movie.rating === "number"
        ? movie.rating.toFixed(1)
        : movie.rating
      : null;

  return (
    <div className="group relative rounded-[4px]" style={{ textDecoration: "none" }}>
      <Link
        to={link}
        aria-label={`${kind}: ${movie.title}`}
        className="absolute inset-0 z-10 rounded-[4px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
      />
      <div className="relative aspect-[2/3] overflow-hidden rounded-[4px] bg-carbon ring-1 ring-inset ring-bone/10 group-hover:ring-projector/50 transition-shadow duration-300 group-hover:shadow-[0_18px_40px_-18px_rgba(0,0,0,0.9)]">
        <LazyLoadImage
          src={posterUrl(movie.poster || posterPlaceholder)}
          alt={movie.title}
          effect="blur"
          wrapperClassName="absolute inset-0 w-full h-full"
          className="w-full h-full object-cover object-center transition-transform duration-500 ease-out group-hover:scale-[1.04]"
        />

        {rating && (
          <div className="absolute top-1.5 right-1.5 z-10 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-[2px] bg-ink/80 backdrop-blur-sm">
            <PiStarFill className="text-projector text-[9px]" />
            <span className="timecode text-[10px] font-medium text-bone/90 leading-none">
              {rating}
            </span>
          </div>
        )}

        <span className="absolute top-1.5 left-1.5 z-10 timecode text-[10px] uppercase tracking-[0.16em] px-1.5 py-0.5 rounded-[2px] bg-ink/70 text-bone/60 backdrop-blur-sm">
          {kind}
        </span>

        <div
          className="absolute inset-0 bg-gradient-to-t from-ink/80 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300"
          aria-hidden="true"
        />

        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none">
          <span className="flex items-center justify-center w-10 h-10 rounded-full bg-projector text-ink shadow-[0_8px_24px_rgba(0,0,0,0.45)] translate-y-1 group-hover:translate-y-0 transition-transform duration-200">
            <BsPlayFill className="text-lg ml-0.5" />
          </span>
        </div>

        <div className="absolute bottom-1.5 right-1.5 z-20">
          <WatchlistButton item={movie} />
        </div>
      </div>

      <div className="mt-2">
        <h3 className="text-[13px] sm:text-sm font-semibold leading-snug text-bone line-clamp-2 group-hover:text-projector transition-colors duration-200">
          {movie.title}
        </h3>
        {movie.episode_label && (
          <p className="mt-1 timecode text-[10px] uppercase tracking-[0.14em] text-projector/85">
            {movie.episode_label}
          </p>
        )}
        {movie.episode_title && (
          <p className="mt-0.5 text-[11px] leading-snug text-bone/55 line-clamp-1">
            {movie.episode_title}
          </p>
        )}
        {!movie.episode_label && movie.release_year && (
          <p className="mt-0.5 timecode text-[10px] text-bone/55">
            {movie.release_year}
          </p>
        )}
      </div>
    </div>
  );
};

export default MovieCard;
