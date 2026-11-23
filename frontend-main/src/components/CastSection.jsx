import React, { useState } from "react";
import { Link } from "react-router-dom";
import { UserRound } from "lucide-react";
import { personImageUrl } from "../utils/imageUrl";

const getInitials = (name = "") =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "?";

function CastCard({ person }) {
  /*
   * The API is inconsistent here: some rows carry `profile` as a full URL, older
   * ones carry `profile_path` as a bare path or null, and rows written through
   * the old queue compactor carry neither. Normalising in one place means the
   * card renders a headshot whenever one exists and falls back to initials
   * otherwise, instead of showing initials because of a field-name mismatch.
   */
  const image = personImageUrl(person);
  const [imageState, setImageState] = useState(image ? "loading" : "error");
  const character = person.character?.trim() || "";
  const department = person.known_for_department?.trim() || "Acting";
  const hasDetail = Boolean(person.id || person.tmdb_id);

  const cardInner = (
    <>
      <div className="relative mx-auto w-20 h-20 sm:w-24 sm:h-24 rounded-full overflow-hidden bg-carbon ring-1 ring-inset ring-bone/15 group-hover:ring-projector/60 transition-[box-shadow] duration-300">
        {imageState === "loading" && (
          <div className="skeleton absolute inset-0" aria-hidden="true" />
        )}
        {image && imageState !== "error" ? (
          <img
            src={image}
            alt={person.name}
            loading="lazy"
            onLoad={() => setImageState("loaded")}
            onError={() => setImageState("error")}
            className={`h-full w-full object-cover transition-all duration-500 group-hover:scale-105 ${
              imageState === "loaded" ? "opacity-100" : "opacity-0"
            }`}
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-1 bg-[radial-gradient(circle_at_30%_20%,rgba(233,184,114,0.22),transparent_60%)] px-2 text-center">
            <span className="text-sm font-bold tracking-wide text-bone">
              {getInitials(person.name)}
            </span>
            <UserRound className="h-3 w-3 text-bone/60" aria-hidden="true" />
          </div>
        )}
      </div>
      <div className="mt-2.5 px-1">
        <p className="line-clamp-2 text-[12px] sm:text-[13px] font-semibold leading-snug text-bone/90 transition-colors group-hover:text-projector">
          {person.name}
        </p>
        {(character || department) && (
          <p className="mt-1 line-clamp-1 timecode text-[10px] uppercase tracking-[0.08em] text-bone/60">
            {character || department}
          </p>
        )}
      </div>
    </>
  );

  const wrapperCls =
    "group block w-20 sm:w-24 text-center rounded-[4px] focus:outline-none focus-visible:ring-2 focus-visible:ring-projector/70 focus-visible:ring-offset-2 focus-visible:ring-offset-ink";

  return (
    <div className="flex-shrink-0 w-20 sm:w-24">
      {hasDetail ? (
        <Link
          to={`/Cast/${person.id || person.tmdb_id}/${encodeURIComponent(person.name)}`}
          className={wrapperCls}
          aria-label={`View all titles with ${person.name}`}
        >
          {cardInner}
        </Link>
      ) : (
        <div className={`${wrapperCls} cursor-default`}>{cardInner}</div>
      )}
    </div>
  );
}

export default function CastSection({ cast = [], crew = [] }) {
  const castList = Array.isArray(cast)
    ? cast.filter((person) => person?.name).slice(0, 20)
    : [];
  /*
   * Crew is read defensively: no current backend response includes a `crew`
   * array (verified across movie/tv/anime/k-drama payloads), so the heading
   * used to promise "& Crew" while showing only actors. Render crew when a
   * response does carry it, and keep the heading honest when it does not.
   */
  const crewList = Array.isArray(crew)
    ? crew.filter((person) => person?.name).slice(0, 12)
    : [];
  if (!castList.length && !crewList.length) return null;

  const total = castList.length + crewList.length;

  return (
    <section className="mt-8 md:mt-12">
      <div className="flex items-end justify-between gap-4 border-b border-bone/10 pb-3 mb-5">
        <div>
          <span className="eyebrow mb-2">Call sheet</span>
          <div className="flex items-baseline gap-2.5">
            <h3 className="section-title text-xl md:text-2xl leading-none">
              {crewList.length ? "Cast & Crew" : "Cast"}
            </h3>
            <span className="timecode text-[10px] text-bone/60">
              {String(total).padStart(2, "0")} names
            </span>
          </div>
        </div>
      </div>

      <div className="flex gap-3 sm:gap-4 overflow-x-auto pb-4 scrollbar-hide">
        {[...castList, ...crewList].map((person, index) => (
          <CastCard
            key={`${person.id || person.tmdb_id || index}-${person.name}`}
            person={person}
          />
        ))}
      </div>
    </section>
  );
}
