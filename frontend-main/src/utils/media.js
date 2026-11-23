import { BASE_URL } from "../config";

// Parse "1080p", "2160p", "4K"... into the numeric size Plyr expects.
export const parseQuality = (quality) => {
  const value = String(quality || "").toUpperCase();
  if (value.includes("4K") || value.includes("2160")) return 2160;
  if (value.includes("2K") || value.includes("1440")) return 1440;
  const match = value.match(/\d{3,4}/);
  return match ? parseInt(match[0], 10) : 0;
};

export const mediaUrl = (file) =>
  `${BASE_URL}/dl/${file.id}/${encodeURIComponent(file.name || "")}`;
