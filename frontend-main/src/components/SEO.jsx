import React from "react";
import { Helmet } from "react-helmet-async";
import { SITENAME } from "../config";

const OG_TYPES = new Set([
  "website",
  "article",
  "video.movie",
  "video.tv_show",
  "video.episode",
]);

/**
 * Absolute URL for a possibly-relative asset.
 *
 * Open Graph consumers (Facebook, WhatsApp, Slack, iMessage) require absolute
 * image URLs and generally refuse SVG — the old default OG image was the
 * site logo as an .svg, so every shared link produced a blank preview card.
 */
function absolute(url) {
  if (!url) return undefined;
  if (/^https?:\/\//i.test(url)) return url;
  if (typeof window === "undefined") return url;
  return `${window.location.origin}${url.startsWith("/") ? url : `/${url}`}`;
}

export default function SEO({
  title,
  description,
  name,
  type,
  keywords,
  image,
  imageAlt,
  noIndex = false,
  jsonLd,
}) {
  // Canonical URLs must point at the page actually being viewed; pages used
  // to pass a hardcoded "https://<site>.com" for every route.
  const canonical =
    typeof window !== "undefined"
      ? `${window.location.origin}${window.location.pathname}`
      : undefined;
  const imageUrl = absolute(image) || absolute("/og-default.png");
  const ogType = OG_TYPES.has(type) ? type : "website";
  const siteName = name || SITENAME;
  const ogImageAlt = imageAlt || title || siteName;

  return (
    <Helmet>
      <title>{title}</title>
      {description && <meta name="description" content={description} />}
      {keywords && <meta name="keywords" content={keywords} />}
      {/*
       * Search, cast and genre pages are infinite near-duplicate listings —
       * every query string produced another indexable URL. Those pass
       * noIndex; everything else stays indexable.
       */}
      <meta
        name="robots"
        content={noIndex ? "noindex, follow" : "index, follow"}
      />
      {canonical && <link rel="canonical" href={canonical} />}

      {/* Open Graph */}
      {canonical && <meta property="og:url" content={canonical} />}
      <meta property="og:site_name" content={siteName} />
      <meta property="og:type" content={ogType} />
      <meta property="og:title" content={title} />
      {description && <meta property="og:description" content={description} />}
      {imageUrl && <meta property="og:image" content={imageUrl} />}
      {imageUrl && <meta property="og:image:alt" content={ogImageAlt} />}
      <meta property="og:locale" content="en_US" />

      {/* Twitter / X */}
      <meta name="twitter:card" content="summary_large_image" />
      <meta name="twitter:title" content={title} />
      {description && <meta name="twitter:description" content={description} />}
      {imageUrl && <meta name="twitter:image" content={imageUrl} />}
      {imageUrl && <meta name="twitter:image:alt" content={ogImageAlt} />}

      {jsonLd && (
        <script type="application/ld+json">{JSON.stringify(jsonLd)}</script>
      )}
    </Helmet>
  );
}
