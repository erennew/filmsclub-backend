import React from "react";
import { motion } from "framer-motion";
import {
  HiChevronLeft,
  HiChevronRight,
  HiChevronDoubleLeft,
  HiChevronDoubleRight,
} from "react-icons/hi2";

const range = (start, end) =>
  [...Array(end - start).keys()].map((el) => el + start);

const getPagesCut = ({ pagesCount, pagesCutCount, currentPage }) => {
  const ceiling = Math.ceil(pagesCutCount / 2);
  const floor = Math.floor(pagesCutCount / 2);
  if (pagesCount <= pagesCutCount) return { start: 1, end: pagesCount + 1 };
  if (currentPage <= ceiling) return { start: 1, end: pagesCutCount + 1 };
  if (currentPage + floor >= pagesCount) return { start: pagesCount - pagesCutCount + 1, end: pagesCount + 1 };
  return { start: currentPage - ceiling + 1, end: currentPage + floor + 1 };
};

const NavBtn = ({ icon: Icon, onClick, disabled, label }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    aria-label={label}
    className={`
      flex items-center justify-center w-9 h-9 rounded-[3px]
      transition-all duration-200
      ${disabled
        ? "text-bone/50 cursor-not-allowed bg-transparent"
        : "text-bone/60 hover:text-bone hover:bg-bone/10 active:scale-95 cursor-pointer"
      }
    `}
  >
    <Icon className="text-base" />
  </button>
);

const Pagination = ({ currentPage, total, limit, onPageChange }) => {
  const pagesCount = Math.ceil(total / limit);
  if (pagesCount <= 1) return null;

  const pagesCut = getPagesCut({ pagesCount, pagesCutCount: 5, currentPage });
  const pages = range(pagesCut.start, pagesCut.end);
  const isFirstPage = Number(currentPage) === 1;
  const isLastPage = Number(currentPage) === pagesCount;

  return (
    <div className="flex items-center justify-center gap-1 mt-8 mb-4 flex-wrap">
      <NavBtn
        icon={HiChevronDoubleLeft}
        onClick={() => onPageChange(1)}
        disabled={isFirstPage}
        label="First page"
      />
      <NavBtn
        icon={HiChevronLeft}
        onClick={() => onPageChange(Number(currentPage) - 1)}
        disabled={isFirstPage}
        label="Previous page"
      />

      {/* Show dots if not starting from 1 */}
      {pagesCut.start > 1 && (
        <span className="w-9 h-9 flex items-center justify-center text-bone/55 text-sm">…</span>
      )}

      {pages.map((page) => {
        const isActive = page === Number(currentPage);
        return (
          <button
            key={page}
            onClick={() => onPageChange(page)}
            className={`
              relative w-9 h-9 rounded-[3px] text-sm font-semibold
              transition-all duration-200 cursor-pointer active:scale-95
              ${isActive ? "text-ink" : "text-bone/50 hover:text-bone hover:bg-bone/10"}
            `}
          >
            {isActive && (
              <motion.div
                layoutId="activePage"
                className="absolute inset-0 bg-projector rounded-[3px]"
                style={{ zIndex: -1 }}
                transition={{ type: "spring", stiffness: 500, damping: 40 }}
              />
            )}
            {page}
          </button>
        );
      })}

      {/* Show dots if not ending at last */}
      {pagesCut.end <= pagesCount && (
        <span className="w-9 h-9 flex items-center justify-center text-bone/55 text-sm">…</span>
      )}

      <NavBtn
        icon={HiChevronRight}
        onClick={() => onPageChange(Number(currentPage) + 1)}
        disabled={isLastPage}
        label="Next page"
      />
      <NavBtn
        icon={HiChevronDoubleRight}
        onClick={() => onPageChange(pagesCount)}
        disabled={isLastPage}
        label="Last page"
      />

      {/* Page count info */}
      <div className="w-full text-center mt-2">
        <span className="timecode text-[11px] text-bone/55">
          Page {currentPage} of {pagesCount}
        </span>
      </div>
    </div>
  );
};

export default Pagination;
