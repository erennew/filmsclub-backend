import React from "react";
import MovieCard from "./MovieCard";
import MovieCardSkeleton from "./MovieCardSkeleton"

const GENRE_LIST = [
  { name: "Action", value: "Action" },
  { name: "Comedy", value: "Comedy" },
  { name: "Horror", value: "Horror" },
  { name: "Drama", value: "Drama" },
  { name: "Anime", value: "Animation" },
  { name: "Romance", value: "Romance" },
  { name: "Thriller", value: "Thriller" },
  { name: "Sci-Fi", value: "Science Fiction" },
];

export default function HomeSection(props) {
  return (
    <>
      {/* Title */}
      <div className="mt-[5rem] flex items-center flex-wrap gap-5 text-primaryTextColor pb-[1.5rem]">
        <div className="pl-[1rem] border-l-2 border-primaryBtn">
          <p className="text-[0.8rem] uppercase font-bold sm:text-[1rem]">
            {props.sectionTitle}
          </p>
        </div>

        <div className="flex items-center flex-wrap gap-8 text-secondaryTextColor">
          {(props.dataType === "movies" || props.dataType === "series") && (
            <div className="flex items-center gap-2 flex-wrap justify-start">
              {[
                { name: "Latest", value: "updated_on" },
                { name: "Top Rated", value: "rating" },
                { name: "New", value: "release_year" },
              ].map((item, index) => (
                <button
                  key={index}
                  onClick={() => {
                    props.setMovieFilterVal(item.value);
                    props.setMovieFilter(item.value);
                  }}
                  className={
                    item.value === props.movieFilterVal
                      ? "py-2 px-2 bg-secondaryTextColor rounded-full text-bgColor text-xs flex-grow transition-all duration-300 ease-in-out hover:bg-primaryTextColor sm:text-sm sm:px-4"
                      : "py-2 px-2 bg-bgColorSecondary rounded-full text-secondaryTextColor text-xs transition-all duration-300 ease-in-out hover:bg-primaryTextColor flex-grow hover:text-bgColor sm:text-sm sm:px-4"
                  }
                >
                  {item.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Genre Filters */}
      {(props.dataType === "movies" || props.dataType === "series") && (
        <div className="flex items-center flex-wrap gap-2 pb-[1.5rem]">
          <button
            onClick={() => {
              if (props.setGenreFilter) props.setGenreFilter("");
            }}
            className={
              !props.genreFilter
                ? "py-1 px-3 bg-primaryBtn rounded-full text-bgColor text-xs transition-all duration-300 ease-in-out sm:text-sm"
                : "py-1 px-3 bg-bgColorSecondary rounded-full text-secondaryTextColor text-xs transition-all duration-300 ease-in-out hover:bg-primaryTextColor hover:text-bgColor sm:text-sm"
            }
          >
            All
          </button>
          {GENRE_LIST.map((g, i) => (
            <button
              key={i}
              onClick={() => {
                if (props.setGenreFilter) props.setGenreFilter(g.value);
              }}
              className={
                props.genreFilter === g.value
                  ? "py-1 px-3 bg-primaryBtn rounded-full text-bgColor text-xs transition-all duration-300 ease-in-out sm:text-sm"
                  : "py-1 px-3 bg-bgColorSecondary rounded-full text-secondaryTextColor text-xs transition-all duration-300 ease-in-out hover:bg-primaryTextColor hover:text-bgColor sm:text-sm"
              }
            >
              {g.name}
            </button>
          ))}
        </div>
      )}

      {/* Movies, series, and all other sections */}
      <div>
        {!props.isMovieDataLoading ? (
          <div className="relative">
            <div className="grid gap-x-2 gap-y-6 grid-cols-2 md:grid-cols-3 bsmmd:grid-cols-4 lg:grid-cols-5 blgxl:grid-cols-6 xl:grid-cols-7">
              {props.movieData.map(
                (movie, index) =>
                    <MovieCard key={index} movie={movie} />
              )}
            </div>
          </div>
        ) : (
          <MovieCardSkeleton />
        )}
      </div>
    </>
  );
}

