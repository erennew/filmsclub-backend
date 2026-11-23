# UI/UX Audit — Cross-Cutting / Systemic Layer

Scope: `tailwind.config.mjs`, `src/index.css`, `index.html`, `vite.config.js`, `package.json`, `src/main.jsx`, `src/App.jsx`, `src/config.js`, `src/components/{SEO,ScrollManager}.jsx`, `src/utils/*`, `src/hooks/*`, plus app-wide greps across `src/**/*.{jsx,js}`.

No source files were modified. Every count below came from a `grep`/token pass over `src/` (107 modules) or a byte-level read of the named file.

## Five corrections to the brief (measured, not assumed)

1. **`fontSize` is inside `extend` — but it still overrides every default.** `tailwind.config.mjs:59-74` sits under `extend`, yet because `fontSize` is a *flat* scale, not a nested one, every key it defines wins over the default. `text-sm` is **0.8rem**, not 0.875rem. `text-2xl` is **1.4rem**, not 1.5rem. Confirmed by reading the compiled values, not inferred.
2. **The `index.html` description is valid UTF-8 — there is no mojibake in the repo.** Byte-level scan of all 137 `.html/.jsx/.js/.css/.json/.webmanifest` files found **zero** double-encoded sequences. The em dash in `index.html:71` is `E2 80 94` (correct U+2014) and the built `.tmp-ui-check/index.html` and `manifest.webmanifest` are byte-clean too. Any `â€"` seen in a terminal is a **console rendering artifact**, not a source defect. The charset *ordering* issue is real; the encoding corruption is not.
3. **`prefers-reduced-motion` is handled** — `src/index.css:1053-1071` kills smooth scroll and clamps all animation/transition durations to `0.01ms`, plus `animation: none` on `.animate-float`/`.animate-glow-pulse`. The gap is that **framer-motion escapes it** (see A6).
4. **`<meta charset>` is at byte 761, not past 1024.** It does satisfy the letter of the HTML spec ("within the first 1024 bytes"), so this is a **fragility** finding (P2), not a live break — see S1 for the honest framing.
5. **Raw hex in `.jsx` is 60 lines / 75 occurrences across 15 distinct values**, not 59 — and `#F0CA98` (21×) is the single worst offender, which matters because it is *not* a Tailwind colour (see D5).

---

## P0 — Blocker

- **[P0] `vite.config.js:180` — the `vendor-react` chunk is 0 KB; React is inlined into the 969 KB entry chunk, defeating the entire manual-chunk strategy.**
  Measured in the real build output `.tmp-ui-check/assets/js/`:

  | chunk | raw | gzip |
  |---|---:|---:|
  | `index-CPgQgCZ-.js` (entry) | **969 KB** | 206 KB |
  | `index-7nAyal4o.css` | **338 KB** | 44 KB |
  | `vendor-nextui-CYnWIFEI.js` | 237 KB | 72 KB |
  | `vendor-router-CErYDYt6.js` | 152 KB | 50 KB |
  | `vendor-motion-D5ncIa4D.js` | 120 KB | 40 KB |
  | `vendor-player-BzRaeAn0.js` | 113 KB | 34 KB |
  | `vendor-swiper-WKiPpvLI.js` | 80 KB | 25 KB |
  | `vendor-axios-D8Bd_SR2.js` | 42 KB | 16 KB |
  | `vendor-icons-8MCvciID.js` | 31 KB | 6 KB |
  | `vendor-lazy-D4laTYf3.js` | 27 KB | 7 KB |
  | **`vendor-react-CUeVt1ch.js`** | **0 KB** | 0 KB |

  `vendor-react` is empty *and* absent from the `<link rel="modulepreload">` list in the built HTML (`vendor-motion`, `-router`, `-nextui`, `-swiper`, `-lazy`, `-icons`, `-axios`, `-player` are all preloaded; `-react` is not). The 969 KB entry is therefore the one chunk that can never be cached independently of app code — every deploy invalidates React, ReactDOM, and all application source in a single hash.
  Fix: split React by resolution rather than by package name, which is the reliable form under Vite 5:
  ```js
  manualChunks(id) {
    if (id.includes("node_modules")) {
      if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return "vendor-react";
      if (id.includes("react-router")) return "vendor-router";
      if (id.includes("framer-motion")) return "vendor-motion";
      ...
    }
  }
  ```
  and verify afterwards that `dist/assets/js/vendor-react-*.js` is ~140 KB and appears in the modulepreload list.

- **[P0] `src/App.jsx:1-19,62-80` — zero route-level code splitting; all 14 routes are statically imported, so every visitor downloads the admin console, Plyr, and Swiper before the homepage paints.**
  `grep` for `React.lazy(` / `import(` across all 107 modules returns exactly **one** dynamic import — `src/main.jsx:25` `import("virtual:pwa-register")`. Everything else is static. Combined with the P0 above, the first paint requires **2,109 KB raw / 494 KB gzip** of JS+CSS before a single pixel of the homepage renders (969 + 338 + 237 + 152 + 120 + 113 + 80 + 42 + 31 + 27).
  Fix: lazy-load the detail and player routes, which are the only ones that need `plyr-react` (113 KB) and `swiper` (80 KB):
  ```jsx
  const MovieDetails = lazy(() => import("./pages/MovieDetails"));
  const InlinePlayer  = lazy(() => import("./components/InlinePlayer"));
  // then wrap <Routes> in <Suspense fallback={<SectionSkeleton />}>
  ```
  Expected effect: entry chunk drops by the `vendor-player` + `vendor-swiper` + detail-page weight (~190 KB raw) from the critical path.

---

## P1 — High

- **[P1] `tailwind.config.mjs:59-74` — `fontSize` silently redefines the entire type scale, and no `lineHeight` is paired with any step.**
  Because this is a flat override, **every** `text-*` utility in the codebase renders at a non-standard size: `xs .75 / sm .8 / base 1 / md .9 / lg 1.1 / xl 1.25 / 2xl 1.4 / 3xl 1.953 / 4xl 2.441 / 5xl 3.052rem`. Two consequences, both live:
  1. `text-sm` is 12.8px, not 14px — **below the 14px comfortable-reading floor** on every secondary label in the app.
  2. Tailwind's default `fontSize` entries carry paired line-heights (`sm` → `1.25rem`). This override supplies none, so all `text-*` utilities fall back to `inherit`/normal leading. That is the mechanism behind the pervasive per-component `leading-snug` / `leading-tight` / `leading-relaxed` overrides scattered through the JSX — each one is a manual patch for the missing pair.
  This is also the direct cause of the arbitrary-size sprawl below: when the named scale is untrustworthy, authors reach for `text-[11px]`.
  Fix: move to the tuple form so sizing and leading travel together, and restore a real scale:
  ```js
  fontSize: {
    xs:   ["0.75rem",  { lineHeight: "1.05rem" }],
    sm:   ["0.875rem", { lineHeight: "1.25rem" }],
    base: ["1rem",     { lineHeight: "1.5rem"  }],
    lg:   ["1.125rem", { lineHeight: "1.6rem"  }],
    xl:   ["1.375rem", { lineHeight: "1.75rem" }],
    "2xl":["1.75rem",  { lineHeight: "2rem"    }],
    // drop the non-standard `md` key; it collides conceptually with the `md` breakpoint
  }
  ```

- **[P1] `src/**/*.jsx` — 286 lines / 299 occurrences of arbitrary `text-[Npx]`, in only 15 distinct sizes; `text-[10px]` alone appears 149 times.**
  Full distribution (token pass over all `.jsx`):

  | token | count | | token | count |
  |---|---:|---|---|---:|
  | `text-[10px]` | **149** | | `text-[15px]` | 2 |
  | `text-[11px]` | **80** | | `text-[17px]` | 2 |
  | `text-[12px]` | 21 | | `text-[9px]` | **18** |
  | `text-[13px]` | 19 | | `text-[11.5px]` | 1 |
  | | | | `text-[19px]`, `text-[2rem]`, `text-[1.3rem]`, `text-[1.7rem]`, `text-[1.75rem]`, `text-[3.8rem]`, `text-[4.6rem]` | 1 each |

  **167 of 299 occurrences (56%) are below 12px**, and `text-[9px]`/`text-[10px]` are frequently combined with `text-bone/35`–`/45` (see A2) — 9px text at ~4.0:1 contrast is unusable on a phone in daylight. The 15 distinct values collapse to roughly 5 real design intents (`10, 11, 12, 13, 17+`), which is strong evidence the scale exists but was never named.
  Fix: add five semantic tokens (`text-micro`, `text-caption`, `text-label`, `text-body`, `text-lead`) with paired line-heights, then codemod `text-[10px]`→`text-caption` etc. The one-offs above 1.7rem (`.3.8rem`, `4.6rem`) belong in `fontSize` as display steps.

- **[P1] App-wide — `bone` is used at **49 distinct opacity steps**, which is not a ramp, it is noise.**
  Measured token counts:

  ```
  TEXT   text-bone 107 · /40 92 · /35 54 · /50 41 · /60 38 · /80 32 · /45 29 · /55 20
         /70 20 · /30 18 · /20 11 · /25 10 · /90 10 · /65 8 · /85 8 · /75 2 · /10 1
  BG     bg-bone/5 30 · /10 31 · /8 12 · /[0.06] 17 · /[0.03] 8 · /[0.04] 6 · /4 4
         /6 2 · /14 2 · /12 1 · /20 2 · /25 2 · /30 1 · /50 1 · /[0.05] 2 · /[0.08] 1 · /[0.12] 1
  BORDER border-bone/10 47 · /8 44 · /6 15 · /15 14 · /12 11 · /20 7 · /25 2 · /18 2
         /30 2 · /60 1 · /5 1 · /35 1
  RING   ring-bone/10 4 · /12 2 · /15 2
  ```

  Note the incoherence at the *same* semantic role: borders exist at `/5 /6 /8 /10 /12 /15 /18 /20 /25 /30 /35 /60` — twelve steps for one job. Backgrounds mix the arbitrary-value and numeric forms of the same alpha (`bg-bone/5` **and** `bg-bone/[0.05]`; `bg-bone/6` and `bg-bone/[0.06]`; `bg-bone/10` and `bg-bone/[0.12]`), so the two forms are already drifting apart.
  Fix: publish a five-step ramp in `tailwind.config.mjs` and delete the rest:
  ```js
  backgroundColor: { bone: { subtle: "rgba(242,237,227,0.04)", muted: "rgba(242,237,227,0.06)",
                             soft: "rgba(242,237,227,0.10)", strong: "rgba(242,237,227,0.16)" } },
  borderColor:     { bone: { subtle: "rgba(242,237,227,0.06)", muted: "rgba(242,237,227,0.10)",
                             strong: "rgba(242,237,227,0.20)" } },
  textColor:       { bone: { faint: "…/0.45", muted: "…/0.55", body: "…/0.70", strong: "…/0.85", full: "#F2EDE3" } },
  ```
  Four background steps + three border steps + five text steps replaces 49 values.

- **[P1] `src/index.css:17-21` vs Tailwind Preflight — a second, hand-written global reset runs *after* Preflight and silently beats it.**
  ```css
  *, { box-sizing: border-box; margin: 0; padding: 0; }
  ```
  Preflight already handles this with lower specificity per-element defaults. Because this `*` block is emitted after `@tailwind base` (which is line 1), it wins, and it strips the `margin` that Preflight intentionally leaves on `h1`–`h6`, `p`, `blockquote`, `figure` — which is *why* the JSX is full of `mb-*`/`mt-*` on every paragraph and heading rather than relying on prose defaults. It also wipes `list-style` inheritance expectations and defeats Preflight's `border-style: solid; border-width: 0` reset ordering.
  Fix: delete lines 17-21. Keep `box-sizing: border-box` only if you want it on pseudo-elements (Preflight already applies it to `*,::before,::after`); let Preflight own margin/padding.

- **[P1] `src/index.css:957-959` — `.skeleton` is defined twice and the second definition (animation only) discards the first definition's gradient.**
  `.skeleton` at **789-798** sets the shimmer gradient *and* `animation: shimmer 1.8s infinite`. `.skeleton` at **957-959** sets only `animation: skeleton-pulse 1.6s ease-in-out infinite`. Same specificity, later wins, so the final computed style is: `animation` from the second rule, `background` from the first (untouched) — but the *shimmer* keyframes are now orphaned while `skeleton-pulse` (opacity flicker) runs against a static gradient. Every skeleton in the app therefore pulses opacity instead of sweeping, and `@keyframes shimmer` is dead weight.
  Same collision shape, verified at: `.loader-episode` **296** vs **962** (the elaborate `--c/--r1/--r2` radial spinner at 296 is completely overridden by a plain border spinner at 962); `.plyr` at **1002 / 1016 / 1030**; `.plyr__video-wrapper` **1009 / 1015**; `.no-scrollbar` **801 / 935**; `.eyebrow` **155 / 168**; `.animate-float` **757 / 1067**; `.animate-glow-pulse` **753 / 1068**.
  Fix: collapse each duplicate into one rule and delete the loser. `@keyframes l4` and `@keyframes shimmer` can then be removed if genuinely unused.

- **[P1] App-wide — the `--text-*` / `--bg-*` CSS-variable token system is referenced ~150 times but **never defined anywhere**.**
  `grep` for the definitions in `index.css` returns **zero matches** for `--text-muted`, `--text-primary`, `--text-secondary`, `--bg-primary`, `--bg-surface`, `--bg-card`, `--border-color`, `--accent-primary`. Usage counts: `--text-primary` 35, `--text-muted` 28, `--border-color` 19, `--bg-surface` 18, `--bg-card` 15, `--accent-primary` 10, `--text-secondary` 9, `--bg-primary` 8.
  So `bg-[var(--bg-primary)]` resolves to nothing, `border-color` falls back to `currentColor`, and `text-[var(--text-muted)]` renders at inherited colour. These are silent no-ops — they render, they just do not do what they say. (They are confined to the unreachable mobile-PWA modules; see H1. That containment is the only reason this is P1 and not P0.)
  Fix: either delete the mobile-v2 modules wholesale (recommended — H1), or define the eight variables in `:root` and map them onto the existing `--ink/--carbon/--bone/--projector` palette so there is one token layer, not two.

- **[P1] `src/pages/*` and `src/components/*` — no `<main>` landmark on any public route.**
  `grep` finds **2** `<main>` tags across 107 modules, and neither is on a homepage/detail route (`src/App.jsx:58` uses `<div className="min-h-screen bg-ink">` as the content wrapper; `src/App.jsx:61` nests a `<div>`). Landmarks present: `<nav>` 12, `<header>` 18, `<footer>` 2, `<section>` 9, `<article>` **0**. Screen-reader users get 12 navs and 18 headers to jump between and no "main content" target at all.
  Fix: `src/App.jsx:61` — change the outer `<div>` wrapper to `<main id="main">`. One-line change, and it makes the skip link in A9 possible.

- **[P1] `index.html:34` — three external `<script>` tags precede the `charset` declaration, which is legal today but one inline edit away from breaking.**
  Byte map of `index.html` (5,800 bytes, **no BOM**, `<!DOCTYPE` at byte 0):

  | byte offset | content |
  |---|---:|
  | 90 | first `<script>` (gtag, `async`) |
  | ~270 | Turnstile `<script async defer>` |
  | ~430 | inline `window.dataLayer` gtag bootstrap |
  | ~700 | `google-site-verification` meta |
  | **761** | **`<meta charset="UTF-8" />`** |
  | 5,358 | `<meta name="description">` |
  | 5,520 | `</head>` |

  The spec requires `charset` within the first 1024 bytes; 761 satisfies that. But the declaration sits *behind* two third-party script tags and an inline script, so the decoder must buffer ~761 bytes before it knows the encoding — and the head has only ~263 bytes of headroom before the 1024 limit is violated. Adding one more preconnect/verification meta or a slightly longer gtag snippet breaks it, at which point the em dash in the description (byte 5,358) becomes a real mojibake bug in browsers that guess `windows-1252`.
  Fix: move `<meta charset="UTF-8" />` to line 4, immediately after `<head>`, before every script. Free, zero-risk, and standard practice.
  ```html
  <head>
    <meta charset="UTF-8" />
    <!-- everything else -->
  ```

- **[P1] `index.html` — no Open Graph or Twitter Card tags in the static document; every share preview depends on JavaScript execution.**
  Confirmed by parsing the built `.tmp-ui-check/index.html`: **zero** `og:` or `twitter:` matches. The tags exist only in `src/components/SEO.jsx:26-58`, injected at runtime by `<Helmet>`. Facebook, LinkedIn, Slack, WhatsApp, Discord and iMessage do **not** execute JS for their unfurlers, so a link to any page renders as a bare URL with no title, no description, no image. Google *does* render JS, so this is a social/referral disaster rather than a search disaster.
  Second-order problem in the same component: `SEO.jsx:22` defaults `og:image` to `` `${origin}/cloudxmovies-logo.svg` `` — **an SVG**. Twitter, Facebook and LinkedIn all reject SVG for `og:image`; they require PNG/JPEG. So even for a JS-executing crawler the default card image is invalid.
  Fix: (a) add static OG/Twitter defaults to `index.html` for the site root, (b) change the `SEO.jsx` default to a 1200×630 PNG and generate one, (c) add real per-page images on the detail routes (`movie.backdrop` at 1200×630). Minimum viable:
  ```html
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="CloudXMovies" />
  <meta property="og:title" content="CloudXMovies — Stream Movies, Series & Anime" />
  <meta property="og:description" content="A late-night screening room for movies, series, anime and K-Dramas." />
  <meta property="og:image" content="/og-image.png" />          <!-- 1200x630 PNG -->
  <meta name="twitter:card" content="summary_large_image" />
  ```

- **[P1] `vite.config.js:125-133` — `/api/` is cached `NetworkFirst` with a 4s timeout and a 24-hour max age, so users get yesterday's catalogue and yesterday's playback URLs.**
  ```js
  urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
  handler: "NetworkFirst",
  options: { cacheName: "cloudx-api", networkTimeoutSeconds: 4,
             expiration: { maxEntries: 120, maxAgeSeconds: DAY }, … }
  ```
  `maxAgeSeconds: DAY` (86,400s) means a slow network (>4s) is served a response up to a day old, with no staleness indication. For a catalogue that is merely wrong-but-harmless; for `/api/quality`-style endpoints returning media URLs it produces dead links that look like player bugs. It also fights the in-memory `apiCache` (`src/utils/apiCache.js:1`, `DEFAULT_TTL = 60_000` = 60s) — two caches with a 1,440× TTL mismatch for the same URLs, so `clearJSONCache()` (called on every app resume by `src/utils/appLifecycle.js:17`) clears the 60s cache while the service worker keeps serving the 24h one. The user sees a refresh do nothing.
  Fix: drop to a short TTL and exclude volatile endpoints:
  ```js
  expiration: { maxEntries: 60, maxAgeSeconds: 5 * 60 },
  networkTimeoutSeconds: 8,
  // and add a second rule ABOVE this one that denies caching for mutable paths:
  // urlPattern: ({url}) => /^\/api\/(hero|quality|sources|admin)\b/.test(url.pathname), handler: "NetworkOnly"
  ```

---

## P2 — Medium

### Design-system inconsistency

- **[P2] App-wide — 18 distinct `border-radius` tokens across 310 occurrences, including 8 arbitrary pixel values that form no scale.**
  Full inventory:

  | token | count | | token | count |
  |---|---:|---|---|---:|
  | `rounded-[3px]` | **79** | | `rounded-[15px]` | 2 |
  | `rounded-full` | 59 | | `rounded-[8px]` | 2 |
  | `rounded-md` (⇒ 6px) | 45 | | `rounded-[10px]` | 2 |
  | `rounded-[4px]` | **44** | | `rounded-r-[3px]` | 2 |
  | `rounded-[2px]` | **32** | | `rounded-[16px]`, `rounded-t-[2px]`, `rounded-[20px]`, `rounded-[5px]`, `rounded-b-[4px]`, `rounded-t-md` | 1 each |
  | `rounded-xl` (⇒12px) | 17 | | | |
  | `rounded-2xl` (⇒16px) | 10 | | | |
  | `rounded-lg` (⇒8px) | 10 | | | |
  | `rounded` (⇒4px) | 4 | | | |

  The near-duplicate pairs are the tell: `rounded-[3px]` (79) vs `rounded` (4) — the same computed 4px-ish radius spelled two ways; `rounded-[4px]` (44) vs `rounded` (4); `rounded-[8px]` (2) vs `rounded-lg` (10); `rounded-[16px]` (1) vs `rounded-2xl` (10). Meanwhile `borderRadius` in the config only adds `"4xl": "2rem"` (`tailwind.config.mjs:85-87`) — **used 0 times**.
  Fix: define a three-step scale and codemod:
  ```js
  borderRadius: { sm: "3px", md: "8px", lg: "16px", full: "9999px" }  // drop `4xl`
  ```
  Mapping: `[2px]/[3px]/[4px]/rounded` → `sm`; `[5px]/[6px]/[8px]/[10px]/md/lg` → `md`; `[15px]/[16px]/[20px]/xl/2xl` → `lg`.

- **[P2] `src/**/*.jsx` — 60 lines / 75 occurrences of raw hex, 15 distinct values, concentrated in exactly the files that should be most tokenised.**
  | value | count | status |
  |---|---:|---|
  | `#F0CA98` | **21** | = `--projector-bright`, **not a Tailwind colour** |
  | `#E9B872` | 12 | = `--projector` / `projector` ✅ |
  | `#229ED9` | 7 | Telegram blue, no token |
  | `#F2EDE3` | 7 | = `--bone` / `bone` ✅ |
  | `#08090B` | 6 | = `--ink` / `ink` ✅ |
  | `#D9A65C` | 6 | = `--projector-deep`, not a Tailwind colour |
  | `#B20710` | 5 | Netflix-red gradient stop, no token |
  | `#E50914` | 3 | Netflix red, no token |
  | `#4FB3E5` | 2 | Telegram light, no token |
  | `#111`, `#0B0C10`, `#0A0B0E`, `#060709`, `#FF705B`, `#FFB457` | 1 each | one-off greys/reds |

  Worst files: `Footer.jsx` 10, `Nav.jsx` 10, `Watch.jsx` 6, `QualityTab.jsx` 5, `InlinePlayer.jsx` 5, `SplashScreen.jsx` 4. Most instances are `hover:bg-[#F0CA98]` — literally a hover state for `bg-projector`, written in hex because `projector-bright` was never promoted from a CSS variable to a Tailwind colour. `#E50914`/`#B20710` (Netflix reds) appear on components that are **unreachable** (H1), which confirms they are paste-ins from another codebase.
  Fix: add `"projector-bright": "#F0CA98"`, `"projector-deep": "#D9A65C"`, and a `telegram: { DEFAULT:"#229ED9", light:"#4FB3E5" }` to `theme.extend.colors`; then codemod `hover:bg-[#F0CA98]` → `hover:bg-projector-bright`.

- **[P2] `src/pages/CastMedia.jsx:62` — `to-projector-deep/10` references a colour that does not exist in the Tailwind theme.**
  ```jsx
  className="… bg-gradient-to-br from-projector/20 to-projector-deep/10 border border-projector/25 …"
  ```
  `projector-deep` exists only as the CSS variable `--projector-deep` (`index.css:11`), never as a `colors` key. Tailwind emits no rule for the stop, so the gradient's end colour silently falls back to `transparent` — the fallback avatar discards its intended amber fade. (`from-projector/20` works because `projector` *is* a colour.) This is the concrete cost of the previous finding: one unreachable-token reference, one silently wrong surface.
  Fix: add the colour (previous item) and the class starts working with no markup change.

- **[P2] `tailwind.config.mjs:31-33` — `font-Inter`, `font-Outfit`, and `font-Anton` are aliases that resolve to entirely different typefaces, plus the three real families are loaded from Google Fonts rather than self-hosted.**
  ```js
  Inter:  ["Manrope", …],   // font-Inter  renders Manrope
  Outfit: ["Manrope", …],   // font-Outfit renders Manrope
  Anton:  ["Fraunces", …],  // font-Anton  renders Fraunces
  ```
  Measured usage: `font-Inter` **0**, `font-Outfit` **0**, `font-Anton` **0** — so no live bug, but any developer who types `font-Anton` expecting Anton gets Fraunces with no warning. Meanwhile the *real* usage is `font-display` (23 files / 31 uses), `font-mono` (15 / 29), and `timecode` (39 files / 176 uses — the dominant typographic utility), against `font-sans` at **0**.
  Fix: delete the three aliases. They are pure liability and cost three `theme` entries.
  Related: `index.html:70` requests **12 font files** in one URL — Fraunces 400/500/600/700 (+`opsz` axis), Manrope 400–800 (5 weights), IBM Plex Mono 400/500/600 — with `display=swap` but **no `<link rel="preconnect">` to `fonts.gstatic.com`**, no `<link rel="preload">` for the two critical faces, and no `size-adjust`/`ascent-override` `@font-face` fallback metrics. Add preconnect + preload for Manrope 400/700 and Fraunces 500, and subset Manrope to 400/600/800 (5 weights × 200 KB is not justified when `font-weight: 500: 0 uses` are indistinguishable at 13px).

- **[P2] App-wide — the vertical rhythm has no scale: `py-24` (12×), `py-20` (7×), `mt-20` (5×), `mt-16` (4×), `mt-14` (5×), `mt-10` (5×) coexist for section-level spacing.**
  Token counts for section-scale spacing: `py-24` 12, `py-20` 7, `pb-24` 5, `mt-20` 5, `mt-16` 4, `mt-14` 5, `mt-10` 5, `mt-8` 6, `mb-8` 5, `pb-24` 5. Meanwhile micro-spacing is dense and consistent (`gap-2` 146, `gap-3` 101, `py-2` 91, `mt-1` 67). So the *component* layer has rhythm and the *page* layer does not — sections land at 80px, 96px, or 56/64px with no pattern, and the irregularity is visible when scrolling the homepage.
  Fix: add a single section macro to `index.css` and use it everywhere instead of ad-hoc `py-*`:
  ```css
  .section { padding-block: clamp(3rem, 6vw, 5rem); }
  .section--tight { padding-block: clamp(2rem, 4vw, 3rem); }
  ```
  Fluid values also remove several breakpoint-specific `md:py-*` overrides.

- **[P2] `vite.config.js:1-16` + `package.json` — the build ships an empty React chunk while `@nextui-org/theme` (a build-time Tailwind plugin, 0 runtime imports) sits in `manualChunks`.**
  `grep` for `from "@nextui-org/theme"` across `src/`: **0 imports**. It is used only as a PostCSS/Tailwind plugin (`tailwind.config.mjs:4,96`). But `vite.config.js:201` lists it in `manualChunks["vendor-nextui"]`, and the resulting chunk is **237 KB** — so the 237 KB is real runtime code (`@nextui-org/popover`, `select`, `button`), not the theme. The point stands as a hazard: adding a non-imported package to `manualChunks` is silently a no-op, and here it masks the fact that NextUI's 237 KB is being shipped for `popover`/`select`/`button` while a hand-rolled design system is used for everything else (see the `btn-primary`/`btn-secondary`/`glass` families in `index.css:836-874`, `103-130`).
  Fix: audit whether the four NextUI components earn 237 KB raw / 72 KB gzip. If `src/` really only uses popover/select/button/modal in a handful of places, hand-roll them and drop the dependency tree — that is a ~72 KB gzip saving on the critical path.

### Accessibility (systemic)

- **[P2] App-wide — 6 opacity steps of `bone` text fail WCAG AA (4.5:1) on both surfaces; the failure threshold is `bone/49`.**
  Measured with the WCAG relative-luminance formula, compositing `#F2EDE3` at alpha over `#08090B` (`--ink`, body) and `#14151A` (`--carbon`, cards/inputs):

  | utility | effective RGB on ink | ratio vs `#08090B` | ratio vs `#14151A` | AA |
  |---|---|---:|---:|:--:|
  | `text-bone` (100%) | 242,237,227 | **15.66:1** | 13.95:1 | ✅ |
  | `text-bone/90` | 218,214,205 | 12.55:1 | 11.18:1 | ✅ |
  | `text-bone/80` | 196,192,184 | 10.03:1 | 8.94:1 | ✅ |
  | `text-bone/70` | 173,170,162 | 7.90:1 | 7.05:1 | ✅ |
  | `text-bone/60` | 150,148,141 | 6.14:1 | 5.48:1 | ✅ |
  | `text-bone/55` | 139,137,130 | **5.05:1** | 4.50:1 | ⚠️ passes on ink, **borderline on carbon** |
  | `text-bone/50` | 125,123,119 | **4.72:1** | **4.32:1 ❌** | ⚠️ fails on cards |
  | `text-bone/45` | 113,112,108 | **4.01:1 ❌** | **3.67:1 ❌** | ❌ |
  | `text-bone/40` | 102,100,97 | **3.36:1 ❌** | **3.07:1 ❌** | ❌ |
  | `text-bone/35` | 90,89,87 | **2.78:1 ❌** | **2.54:1 ❌** | ❌ |
  | `text-bone/30` | 78,77,76 | 2.26:1 ❌ | 2.06:1 ❌ | ❌ |
  | `text-bone/25` | 66,66,64 | 1.79:1 ❌ | 1.63:1 ❌ | ❌ |
  | `text-bone/20` | 55,55,53 | 1.40:1 ❌ | 1.28:1 ❌ | ❌ |

  Also measured: placeholder `rgba(242,237,227,0.4)` over `--carbon` (all inputs are forced to `background-color: var(--carbon) !important` by `index.css:49`) = **3.45:1** ❌; `text-projector/70` over ink = **5.71:1** ✅; `.eyebrow` colour `rgba(233,184,114,0.9)` over ink = **8.92:1** ✅.

  Instances failing AA in real usage — `text-bone/45` **29**, `/40` **92**, `/35` **54**, `/30` **18**, `/25` **10**, `/20` **11**, plus `text-bone/50` **41** on card surfaces = **255 failing instances**. Worst files: `Nav.jsx` 20, `admin/ManualAddPage.jsx` 15, `MoviesAndSeriesDetailsSections.jsx` 13, `MobileSearch.jsx` 11, `admin/IndexingPage.jsx` 8, `admin/CurationPage.jsx` 8.
  Severity is amplified because these classes are overwhelmingly applied to **9–11px** text (`text-bone/35` co-occurs with `text-[10px]`/`text-[11px]` in `admin/CommandsPage.jsx:68,122,167`, `admin/CurationPage.jsx:176,231,300`, `admin/DashboardPage.jsx:21`, `admin/AdminShell.jsx:207`, `components/Nav.jsx:444,484`).
  Fix: (a) remap `bone/35` → `bone/60` (lowest step that clears AA on both surfaces) and `bone/40`,`/45` → `bone/60`; `bone/50` → `bone/60` for anything on `--carbon`; (b) raise the offending 9–10px sizes to ≥12px; (c) encode the floor in the ramp so it cannot regress — see the D2 fix.

- **[P2] App-wide — `focus-visible:outline-none` on 4 components actively cancels the one global focus ring the app has.**
  The app *does* have a correct global fallback — `src/index.css:829-833`:
  ```css
  *:focus-visible { outline: 2px solid var(--projector); outline-offset: 2px; border-radius: 4px; }
  ```
  But four components pair it with an explicit kill:
  - `src/components/MovieCard.jsx:29` — `… focus-visible:outline-none focus-visible:ring-2 …` (+ `ring-offset-2`)
  - `src/components/TopTenToday.jsx:94` — same pattern
  - `src/components/CastSection.jsx:67` — `focus-visible:outline-none` with **no ring replacement at all**
  - `src/components/HomeHero.jsx:80` — `focus-visible:outline-none` with **no ring replacement at all**

  `CastSection.jsx:67` and `HomeHero.jsx:80` are the real defects: they remove the global outline and substitute nothing, so keyboard focus on the cast carousel and hero controls is invisible. `MovieCard`/`TopTenToday` are acceptable (ring replaces outline) but inconsistent in mechanism.
  Totals: `outline-none`/`focus:outline-none`/`focus-visible:outline-none` **13 sites** vs `focus-visible:ring` **3 sites** (`MovieCard:29`, `TopTenToday:94`, `admin/MediaLibraryPage:285`) plus 2 `focus-visible:ring` inside `admin/ui.jsx:94` and `SettingsPage:125` that use `focus:border` instead.
  Fix: delete `focus-visible:outline-none` at `CastSection.jsx:67` and `HomeHero.jsx:80` — the global rule then applies. Standardise the other two on the ring form and leave `outline` alone.

- **[P2] App-wide — zero `aria-expanded` / `aria-controls` on any disclosure, dropdown, or nav toggle.**
  `grep` across all 107 modules: `aria-expanded` **0**, `aria-controls` **0**, `aria-haspopup` **0**. Meanwhile the UI contains 12 `<nav>` elements, a mobile search overlay toggled by `Nav.jsx:378` (`setIsMobileSearchOpen(true)`), quality/audio/subtitle dropdowns, and filter panels in the admin console. Screen-reader users cannot tell whether any of these is open or closed. Only 3 `aria-pressed` exist (`WatchlistButton.jsx:41,64`, `admin/IndexingPage.jsx:146`) and 3 `aria-current` (`Nav.jsx:687`, `HomeHero.jsx:79,113`) — the patterns were understood, just not applied to disclosures.
  Fix: on every toggle button add `aria-expanded={open}` and `aria-controls="<panel-id>"`, and give the panel `id="<panel-id>"`.

- **[P2] App-wide — 239 `<button>` elements, 41 `aria-label`s total across *all* elements, and no accessible name on most icon-only controls.**
  Counts: `<button` **239**, `aria-label` **41**, `aria-hidden` **25**, `role=` **9**, `tabIndex` **1**, `aria-live` **1**. `role="button"` count is **0**, and `<div onClick>`/`<span onClick>` are also **0** (252 `onClick` total, all on real `<button>`/`<Link>`) — so the *element-choice* discipline is good; the gap is naming. `aria-live="1"` for a media app with toasts, async search results (`Nav.jsx` search dropdown), and a downloader is effectively no live region at all.
  Fix: add `aria-label` to every icon-only button; add `role="status" aria-live="polite"` to the search-results container and the toast host.

- **[P2] `src/components/MoviesAndSeriesDetailsSections.jsx` and `src/components/HomeHero.jsx` — duplicate `<h1>` per page.**
  `h1` counts per module: `MoviesAndSeriesDetailsSections.jsx` **2**, `Token.jsx` **2**, and `HomeHero.jsx` **1** while `MoviesAndSeriesSections.jsx` also carries **1**. On a detail route both the hero title and the section title are `h1`, giving two top-level headings; on the homepage `SplashScreen.jsx` (rendered by `Home.jsx`) adds a third `h1`. Heading order also jumps in places: `MobileSearch.jsx` has **0** `h1` and one `h3`; `OfflineFallback.jsx` 0 `h1` / one `h3`; `NotificationSystem.jsx` opens with an `h4` and has no `h1`–`h3` above it.
  Fix: exactly one `h1` per route (the media title on detail pages, the hero headline on home); demote the rest to `h2`, and fix the two `h3`-first files.

- **[P2] 5 of 10 `target="_blank"` links omit `rel="noopener"`.**
  Sites with `rel="noopener noreferrer"`: `Footer.jsx:69`, `QualityTab.jsx:111,121,132,173`. Sites **without** it: `admin/ManualAddPage.jsx:85,95`, `admin/MediaEditorPage.jsx:402,411`, `admin/OrphansPage.jsx:252`. Modern browsers imply `noopener` for `target=_blank`, so this is not exploitable today, but it is a one-line hardening gap and it means the *new tab cannot use `window.opener`* behaviour is inconsistent across the app. The JS-opened windows are correct: `Buttons.jsx:52`, `TelegramButtons.jsx:14`, `MoviesAndSeriesDetailsSections.jsx:235,240` all pass `"noopener noreferrer"`.
  Fix: add `rel="noopener noreferrer"` to the five `<a>` tags.

### Performance / CLS

- **[P2] `src/index.css:232-293` — three loader animations animate `box-shadow`, a non-composited property, forcing a repaint on every frame instead of a GPU transform.**
  ```css
  .loader { box-shadow: 0 0 0 0 var(--projector); animation: l2 1.5s infinite linear; }
  .loader:before, .loader:after { box-shadow: 0 0 0 0 var(--projector); animation: inherit; }
  @keyframes l2 { 100% { box-shadow: 0 0 0 40px rgba(233,184,114,0.18); } }
  ```
  `box-shadow` cannot be composited; each of the 3 elements (`.loader`, `:before`, `:after`) repaints a growing 40px-radius shadow 60×/s. The same pattern repeats verbatim for `.loader-search` (`l3`, 30px radius, `index.css:264-293`). On a mid-range Android this is a measurable main-thread cost on exactly the screens where the user is already waiting.
  Fix: animate a pseudo-element's `transform: scale()` + `opacity` instead:
  ```css
  @keyframes ring { from { transform: scale(.4); opacity:.6 } to { transform: scale(1); opacity:0 } }
  .loader::before { content:""; position:absolute; inset:0; border-radius:inherit;
                    border: 2px solid var(--projector); animation: ring 1.5s infinite; }
  ```
  (Also free: `.loader:before/:after` already exist, so no markup change is needed.)

- **[P2] `src/index.css` 13 `backdrop-filter` declarations + 26 `backdrop-blur-*` utilities — the blur stack is the mobile paint bottleneck.**
  CSS declarations at lines 105/106, 112/113, 119/120, 126/127, 360, 406, 522, 568, 866; JSX utilities `backdrop-blur-md` 8, `-xl` 7, `-sm` 5, `-2xl` 5, `-lg` 1. Worst offenders by intent: `.glass-dark` (blur 20px, `index.css:117-122`) and `backdrop-blur-2xl` (24px) both used on overlays/modals that cover the viewport — a full-screen 20–24px backdrop blur on a mid-range phone costs ~4–8 ms/frame and blocks the compositor.
  Compounding factor: `.film-grain::after` (`index.css:142-152`) layers a `feTurbulence` SVG noise texture at `opacity:0.16` with `mix-blend-mode: overlay` over the hero — `mix-blend-mode` plus `backdrop-filter` on overlapping layers is the single most expensive combination in CSS.
  Fix: cap blur at 12px (`backdrop-blur-md`) and reserve it for small surfaces (the nav bar, one dropdown). Replace full-screen overlay blur with a plain `rgba(8,9,11,0.86)` scrim — visually near-identical over video, ~5 ms/frame cheaper. Gate the grain behind a coarse-pointer/`prefers-reduced-motion` check or drop `mix-blend-mode`.

- **[P2] `public/` — 4.58 MB of static assets served on every fresh install; two screenshots alone are 2.8 MB of PNG.**
  Measured: `public/` 48 files / 4.58 MB, of which **32 PNGs = 4.38 MB**.

  | file | size |
  |---|---:|
  | `screenshots/home-wide.png` (1920×1080) | **1,496 KB** |
  | `screenshots/home-narrow.png` (1080×1920) | **1,346 KB** |
  | `cloudxmovies-logo.svg` | 177 KB |
  | `splash/apple-splash-2048x2732.png` | 138 KB |
  | `splash/apple-splash-1668x2388.png` | 109 KB |
  | `splash/apple-splash-1640x2360.png` | 107 KB |
  | `splash/apple-splash-1536x2048.png` | 102 KB |
  | 8 more `apple-splash-*.png` | ~700 KB combined |

  The two screenshots are declared in the PWA manifest (`vite.config.js:83-101`) as `form_factor: narrow` / `wide` for the rich install prompt. At 1080×1920 and 1920×1080 they should be WebP or palette-optimised PNG — the same images at quality 80 WebP land at ~80–120 KB each, a **~2.5 MB saving**. The 14 iOS splash PNGs (~1.1 MB) are correct to keep but can be quantised heavily (they are flat dark gradients).
  Mitigating note (accurate): `vite.config.js:107-114` `globIgnores` already excludes `splash/**`, `screenshots/**`, `cloudxmovies-logo.svg`, `oldcloudx.svg`, `vite.svg` from the service-worker precache, so these do not bloat the SW cache — they are still served over the network and still cost real bandwidth.
  Fix: convert both screenshots to WebP ≤150 KB and update the manifest `type` to `image/webp`; run the splash PNGs through an 8-bit quantiser; delete `public/vite.svg` (866 B, unreferenced) and `public/oldcloudx.svg` (2.6 KB, unreferenced).

- **[P2] `src/assets/lotte/404.json` — 293 KB of Lottie JSON loaded on the 404 page, unminified and uncompressed.**
  Measured `src/assets/`: 11 files / 0.64 MB — `lotte/404.json` **293 KB**, `images/google.png` 87 KB, `images/skeletonLight.png` 73 KB, `lotte/happy.json` 62 KB, `images/detailPosterPlaceholder.png` 41 KB, `images/logo.png` 31 KB, `logo.jpg` 25 KB, `images/l.png` 20 KB, `lotte/sad.json` 16 KB, `poster-placeholder.png` 8 KB, `react.svg` 4 KB.
  `404.json` is imported by `src/pages/NotFound.jsx` (reachable route `*`). A 293 KB JSON parses to a much larger JS object graph on the main thread — for a page whose only job is "go home". `happy.json` + `sad.json` (78 KB) are imported only by the dead `Token.jsx`.
  Fix: replace the 404 Lottie with a small CSS/SVG animation (the app already has `.animate-float` and a keyframe library), or at minimum re-export the Lottie with reduced layer/point counts (404 illustrations routinely compress 5–10×). Delete `happy.json`/`sad.json` with `Token.jsx`.

- **[P2] `index.html:44-58` + `vite.config.js:18-102` — the PWA head carries 14 `apple-touch-startup-image` links and 4 manifest `shortcuts` pointing at icons that are not in `globIgnores`.**
  The manifest declares shortcuts to `/Movies`, `/Series`, `/anime`, `/k-drama` with `icons: /icons/shortcut-{movies,series,anime,kdrama}.png`. Those four files are **not** in the `globIgnores` list (`vite.config.js:107-114` only excludes `icons/icon-*.svg`), so they are precached. Combined with `globPatterns` including `png` and `maximumFileSizeToCacheInBytes: 6 * 1024 * 1024` (`vite.config.js:122`), the precache budget is generous enough to be wasteful on first install.
  Fix: add `**/icons/shortcut-*.png` to `globIgnores` if the shortcuts are not worth precaching, and lower `maximumFileSizeToCacheInBytes` to `2 * 1024 * 1024` — nothing in the current precache set legitimately exceeds that (the largest precachable asset is `favicon.ico` at 11 KB, since the 1.5 MB screenshots are already excluded).

### SEO / meta

- **[P2] `public/robots.txt:9` — references `/sitemap.xml`, which does not exist.**
  ```
  User-agent: *
  Allow: /
  Disallow: /api/  /dl/  /watch/  /admin  /login  /signup
  Sitemap: /sitemap.xml
  ```
  `Test-Path public\sitemap.xml` → **False**, and there is no `dist/sitemap.xml`. Every crawl hits a 404 on the declared sitemap. There is also **no** `public/_redirects`, `public/_headers`, `vercel.json`, `netlify.toml`, or `.htaccess` anywhere in the repo — so nothing guarantees the SPA rewrite that `navigateFallback` provides *only for service-worker-controlled clients*. A cold crawler or a user with a cleared cache hitting `/mov/123` directly depends entirely on host configuration that is not in the repo.
  Two further issues in the same file: the `Sitemap:` directive must be an **absolute URL** per the robots.txt spec, and `Disallow: /watch/` is listed although no `/watch/` route exists in `src/App.jsx:63-79` (dead directive).
  Fix: generate a real sitemap at build time (a Vite plugin or a `sitemap.xml` listing the 14 static routes), make the directive absolute (`Sitemap: https://<host>/sitemap.xml`), drop the `/watch/` line, and add a host rewrite config (`public/_redirects` with `/* /index.html 200` for Netlify, or a `vercel.json` rewrite) so deep links work without the service worker.

- **[P2] Brand identity is three different products depending on where you look.**
  | location | brand |
  |---|---|
  | `package.json:2` | `"name": "fyvio"` |
  | `index.html:37,40,66,71`, `SITENAME` in `src/config.js` | **CloudXMovies** |
  | `src/pages/MobileProfile.jsx:226` | `Fyvio Streaming v1.0.0` |
  | `src/admin/api.js:1`, `src/admin/ui.jsx:215` | `FilmsClub backend` / `filmsclub-backend` |
  | repo root `_teluguflix_home.html` | `TeluguFlix` |

  Measured in `src/`: `CloudXMovies` 4 lines / 5 files (`MobileProfile.jsx`, `config.js`, `SplashScreen.jsx`, `Nav.jsx`, `SEO.jsx`), `CloudX` 10 lines, `fyvio` 1 line, `TeluguFlix` **0** (so the TeluguFlix artifact is repo-root only — the brief's concern there is confined to `_teluguflix_home.html`). The live inconsistency is `MobileProfile.jsx:226` rendering "Fyvio Streaming" — though that module is unreachable (H1), so the *rendered* product says CloudXMovies everywhere.
  Fix: set `package.json` `"name": "cloudxmovies"`; delete or rename `MobileProfile.jsx`; keep `filmsclub-backend` as an internal repo name only. Confirm the intended public brand before a launch, because "Fyvio" appearing in a version string is the kind of thing that ships accidentally.

- **[P2] `src/components/SEO.jsx:14` — canonical URL is computed from `window.location` at render time, so all canonical/`og:url` output is client-side only.**
  ```js
  const canonical = `${window.location.origin}${window.location.pathname}`;
  ```
  Correct in spirit (a comment notes pages used to hardcode `https://<site>.com`), but it means: no canonical in the static HTML, and the tag depends on JS. For Google this is fine (it renders), but canonical tags delivered by JS are explicitly weaker signals than static ones, and non-rendering crawlers see none.
  Fix: keep the dynamic value for detail routes but emit a static `<link rel="canonical" href="https://<host>/" />` in `index.html` for the shell; alternatively inject per-route `<link rel="canonical">` at build time via a prerender step.

- **[P3] `index.html:35` — `<meta name="referrer" content="no-referrer" />` sends no referrer to any outbound link or analytics.**
  This strips `Referer` on every navigation out of the app, including to the Telegram links (`src/config.js` `TG_URL`, rendered by `Footer.jsx:66`) and to the `@vercel/analytics` endpoint (`src/App.jsx:29,85`). Any partner attribution and most organic-referral reporting in Google Analytics 4 is lost — GA4 will bucket all outbound/referral traffic as direct.
  Fix: `content="strict-origin-when-cross-origin"` (the modern default) unless there is a specific privacy requirement; that keeps path privacy cross-origin while preserving attribution for same-origin navigations.

- **[P3] No structured data (JSON-LD) anywhere despite a fully typed media catalogue.**
  `grep` for `application/ld+json` across `src/` and `index.html`: 0 matches. The app knows `tmdb_id`, `title`, `release_year`, `rating`, `poster`, `backdrop`, `genres`, `media_type` for every item, and `SEO.jsx` already threads `OG_TYPES` including `video.movie` / `video.tv_show` (`SEO.jsx:18`).
  Fix: emit `Movie`/`TVSeries` schema on the two detail routes — the data is already in scope in `MovieDetails.jsx`/`TvDetails.jsx`, so it is a `<script type="application/ld+json">` next to the existing `<SEO>` call. This is the highest-leverage SEO change available and costs no new fetch.

### Service worker / PWA

- **[P2] `vite.config.js:119-121` — `clientsClaim: true` with `skipWaiting: false` is an internally contradictory update model that the prompt UI does not account for.**
  ```js
  cleanupOutdatedCaches: true,
  clientsClaim: true,
  skipWaiting: false,
  ```
  `skipWaiting:false` means a new SW waits until every tab closes; `registerType:"prompt"` (`vite.config.js:12-14`) is the matching choice. But `clientsClaim:true` makes the *new* SW claim all clients as soon as it activates — so the moment the last old tab closes and the new SW activates, every subsequently-opened page is controlled by the new version **without** the user ever seeing the "update available" toast. The prompt is therefore advisory, not authoritative; a user who dismisses it still gets the new build on their next page load, which makes the "keeps installed clients on current build until user accepts" comment inaccurate.
  Fix: choose one model. For prompt-driven updates use `skipWaiting:false, clientsClaim:false` and call `updateSW(true)` on accept (which `src/main.jsx:49` already does via `pwa:apply-update`).

- **[P2] `src/main.jsx:36-43` — the 1-hour update poll is never cleared and is duplicated per registration.**
  ```js
  onRegisteredSW(_swUrl, registration) {
    if (!registration) return;
    const checkForUpdate = () => registration.update().catch(() => {});
    window.setInterval(checkForUpdate, 60 * 60 * 1000);   // never cleared
    document.addEventListener("visibilitychange", …);      // never removed
  }
  ```
  `setInterval` returns a handle that is discarded, and `visibilitychange` adds a listener that is never removed. This is mostly benign in a single-page app (one registration per document lifetime), but it is a leak in the presence of HMR/StrictMode double-invocation in dev, and the visibility listener means `registration.update()` can fire on every tab focus *in addition* to the hourly poll — unbounded network calls to the SW script.
  Fix: store and clear the handle, and dedupe the listener by comparing state first (`if (document.visibilityState === "visible")`). Also consider dropping the poll to `updateViaCache: "none"` + a 6-hour interval; hourly is aggressive for a media catalogue.

- **[P2] `src/utils/apiCache.js:1` + `vite.config.js:128` — two caches with a 1,440× TTL mismatch for the same URLs, and the API cache is never invalidated on admin-token change.**
  In-memory: `DEFAULT_TTL = 60_000` (60 s). Service worker: `maxAgeSeconds: DAY` (86,400 s). `startContentRefresh()` (`src/utils/appLifecycle.js:12-16`) calls `clearJSONCache()` on every `visibilitychange`/`pageshow`, which clears the 60 s cache — and then the fetch it triggers is answered from the service worker's 24 h `/api/` cache. **The refresh does nothing.** This is the mechanism that makes "app resume" feel broken to users.
  Separately, `src/config.js:59-72` stores an admin API base in `localStorage` (`cx_admin_api_base`) via `setAdminBase`, and `src/admin/api.js` holds an admin token — but no code clears `cloudx-api` when either changes. On a shared device, a logged-out admin's cached admin responses remain readable from the SW cache.
  Fix: (a) align the TTLs (previous item), (b) send `Cache-Control: no-store` expectations by excluding `/api/admin/*` from caching, (c) on logout/token change, call `caches.delete("cloudx-api")` alongside `clearJSONCache()`.

- **[P2] `vite.config.js:115-118` — offline deep links work, but there is no offline *route*: `OfflineFallback.jsx` is unreachable and the `pwa:offline-ready` event has no listener.**
  The good news, verified: `navigateFallback: "/index.html"` with `navigateFallbackDenylist: [/^\/api\//, /^\/dl\//, /^\/admin/]` is **correct**. `/mov/123` is not denied, so an offline deep link resolves to the precached `index.html` and the React router renders `MovieDetails`; `/api/` and `/dl/` correctly bypass the fallback (serving `index.html` for a media URL would be a bug), and `/admin` is correctly denied with a documented rationale. This is the strongest part of the PWA config.
  The gap: `src/main.jsx:33-35` dispatches `pwa:offline-ready`, and `src/utils/useOnlineStatus.js` tracks connectivity, but `src/pages/OfflineFallback.jsx` (142 lines, a designed offline screen) is imported by **nothing**, and no listener exists for `pwa:offline-ready` anywhere in `src/`. So an offline deep link renders the real `MovieDetails` shell and then fails each `/api/` fetch — the user gets skeletons and error toasts rather than the offline screen that was built for this.
  Fix: route the offline case rather than the event — in `MovieDetails`/`TvDetails`, when `!isOnline` render `<OfflineFallback />` instead of the skeleton, or register the component in `App.jsx` behind the existing `useOnlineStatus` hook.

### Dead code / hygiene

- **[P2] 17 modules are imported by nothing — and they drag ~400 lines of CSS, an entire undefined token system, and the app's only offline UI with them.**
  Verified by `grep` for `from "...<Name>"` across all 107 modules; "imported by" column is the complete inbound-import set:

  | module | lines | imported by | consequence |
  |---|---:|---|---|
  | `src/pages/MobileDownloads.jsx` | 314 | **nothing** | uses 8 undefined `--*` vars |
  | `src/pages/MobileProfile.jsx` | 233 | **nothing** | renders "Fyvio Streaming v1.0.0" |
  | `src/pages/MobileSearch.jsx` | 254 | **nothing** (`Nav.jsx` has its own inline overlay at `:512`) | 8 `text-bone/35` |
  | `src/pages/OfflineFallback.jsx` | 142 | **nothing** | **the offline UI is dead** |
  | `src/components/MobileMovieDetails.jsx` | 239 | **nothing** | 20+ `var(--bg-*)` |
  | `src/components/MobileBottomNav.jsx` | 88 | **nothing** | Netflix red `#E50914` |
  | `src/components/BottomNav.jsx` | ~50 | **nothing** | `text-primaryBtn` |
  | `src/components/PullToRefresh.jsx` | ~130 | **nothing** | `var(--text-muted)` |
  | `src/components/NotificationSystem.jsx` | ~175 | **nothing** | only `h4` in app |
  | `src/components/PageTransition.jsx` | ~50 | **nothing** | — |
  | `src/components/AudioTrackSelector.jsx` | ~120 | **nothing** | **owns 310 lines of `index.css`** |
  | `src/components/SubtitleSelector.jsx` | ~130 | **nothing** | **owns 310 lines of `index.css`** |
  | `src/components/TrailerModal.jsx` | ~95 | **nothing** | — |
  | `src/components/GenreSection.jsx` | ~90 | **nothing** | — |
  | `src/hooks/useTouchGestures.jsx` | 144 | **nothing** | — |
  | `src/components/Logout.jsx` | ~115 | **nothing** | imports firebase + AuthContext |
  | `src/components/PrivateRoute.jsx` | ~30 | **nothing** (App.jsx:25 commented) | — |

  Cross-check that these are genuinely unreachable, not dynamically referenced: `AudioTrackSelector`/`SubtitleSelector` appear in no `from` clause and no JSX tag — `MoviesAndSeriesDetailsSections.jsx` uses `AudioTracks.jsx` (a *different*, reachable component) and `InlinePlayer.jsx` renders its own inline track menus at `:269-359`. `MobileSearch` is a real duplicate: `Nav.jsx:512-535` implements the mobile search overlay inline, with 16 supporting `isMobileSearchOpen` references.
  Also unreachable via the same test: `src/pages/Login.jsx` (App.jsx:19 commented), `src/pages/Token.jsx` (App.jsx:17 commented), `src/context/AuthContext.jsx` (App.jsx:27 commented — reachable only from the dead Login/Logout/PrivateRoute/signInWIthGoogle set), `src/components/signInWIthGoogle.jsx`, `src/admin/LoginPage.jsx`. **This resolves the open question for the Login/Token auditors: those routes are not registered in `src/App.jsx:62-79`, so both pages are unreachable in the shipped app.**
  Fix: delete the 17 modules plus `Login.jsx`, `Token.jsx`, `AuthContext.jsx`, `signInWIthGoogle.jsx`, `admin/LoginPage.jsx`, and — crucially — then delete `index.css:341-653` (the `.audio-track-*`/`.subtitle-*` block, ~310 lines) and the undefined `--text-*`/`--bg-*` references, which exist *only* to serve these files. This single change removes ~2,300 lines of dead source, 310 lines of dead CSS, and the entire second token system.

- **[P2] `src/index.css:341-653` — ~310 lines of hand-written `.audio-track-*` / `.subtitle-*` CSS for components that are unreachable, duplicating what Tailwind already does.**
  Confirmed dead: `.audio-track-selector`, `.audio-track-button`, `.audio-track-dropdown`, `.audio-track-option` and the parallel `.subtitle-*` family are referenced **only** by `AudioTrackSelector.jsx` and `SubtitleSelector.jsx`, both unreachable (previous finding). Measured: 20 `audio-track` selector lines + 23 `subtitle` selector lines in `index.css`, spanning lines 341–653 — **313 lines**, including 4 `backdrop-filter` declarations (lines 360, 406, 522, 568) and 2 responsive blocks.
  Beyond being dead, the block is a Tailwind-migration candidate: each rule re-implements flex/gap/padding/border/transition that Tailwind expresses in the JSX (`.audio-track-button` alone sets `display, align-items, gap, padding, background, border, border-radius, cursor, font-size, transition`).
  Fix: delete lines 341-653 with the components. If audio/subtitle selection is a planned feature, rebuild it on the *reachable* `InlinePlayer.jsx:269-359` markup, which already does this with Tailwind utilities and no CSS file.

- **[P2] `package.json:20,33,34` — three dependencies are reachable only from dead code, and one is entirely unused.**
  | package | version | imports in `src/` | verdict |
  |---|---|---|---|
  | `react-ga` | `^3.3.1` | **0 files, 0 lines** | **remove** — dead dependency |
  | `uuid` | `^10.0.0` | 1 file: `pages/Token.jsx:10,52` | dead (Token unreachable) |
  | `lottie-react` | `^2.4.0` | 2 files: `pages/NotFound.jsx:3`, `pages/Token.jsx:11` | live only via NotFound (293 KB `404.json`) |
  | `react-toastify` | `^10.0.5` | 7 lines / 5 files | **live** — `ToastContainer` rendered at `Home.jsx:99`, `MovieDetails.jsx:149`, `TvDetails.jsx:171` |
  | `plyr-react` | `^5.3.0` | 2 files: `InlinePlayer.jsx:1,2`, `Watch.jsx:3,4` | live via `MoviesAndSeriesDetailsSections.jsx` |
  | `@vercel/analytics` | `^2.0.1` | 1 file: `App.jsx:29` (`<Analytics />` at `:85`) | live but see S5 (no referrer) |

  `react-toastify` needs a note against the brief's suspicion: although `App.jsx:30` has the import commented out, three reachable pages each render `<ToastContainer>` and `MovieDetails.jsx:4`/`TvDetails.jsx:4` import it — so the dependency is genuinely used, and the *actual* hygiene issue is that `ToastContainer` is mounted **three times on three routes instead of once in the shell**, which duplicates the container and its CSS (`react-toastify/dist/ReactToastify.css` imported at `Home.jsx:20`, `MovieDetails.jsx:5`, `TvDetails.jsx:5`).
  Fix: remove `react-ga`; remove `uuid` and `lottie-react`'s Token usage with the dead pages; hoist `<ToastContainer>` into `src/App.jsx` once (uncomment `App.jsx:30`) and delete the three per-page instances plus their three CSS imports.

- **[P3] `src/assets/` — 5 assets totalling ~415 KB are unreferenced.**
  Verified by grepping the basename across `src/` and `public/`: **unused** — `react.svg` (4 KB), `logo.jpg` (25 KB), `images/l.png` (20 KB), `images/skeletonLight.png` (73 KB), `images/detailPosterPlaceholder.png` (41 KB). Used: `images/google.png` (`signInWIthGoogle.jsx`, itself dead), `images/logo.png` (`Login.jsx`, dead), `images/poster-placeholder.png` (4 live files), `lotte/404.json` (`NotFound.jsx`), `lotte/{happy,sad}.json` (dead `Token.jsx`).
  Fix: delete the 5 unused files; after H1, `google.png` (87 KB), `logo.png` (31 KB), `happy.json`, `sad.json` also become deletable — ~260 KB more.

- **[P3] `src/index.css` — 6 animation utilities and 6 component classes are defined but never used from any JSX.**
  Defined with zero JSX references (verified by class-name grep across all `.jsx`): `.animate-fadeIn`, `.animate-fadeInUp`, `.animate-slideUp`, `.animate-slideInLeft`, `.animate-scaleIn` (`index.css:733-751`), `.card-hover` (`192-199`), `.premium-card` (`766-778`), `.section-line` (`908`), `.hairline` (`187`), `.loader-search` (`264-293`). The only animation utility that *is* used is `.animate-splash-sweep` (`SplashScreen.jsx:108`) plus `.animate-float`/`.animate-glow-pulse` (which exist solely to be disabled by the reduced-motion block at `:1067-1068` — so they are defined, never applied, and then specially un-applied).
  Fix: delete the 5 unused `@keyframes` (`fadeIn`, `fadeInUp`, `slideUp`, `slideInLeft`, `scaleIn`) and the classes; keep `.animate-splash-sweep`. `.animate-float`/`.animate-glow-pulse` can go with them, which also lets the reduced-motion block shrink.

- **[P3] 148 ESLint warnings, 0 errors — dominated by `React` imports that React 18's JSX transform makes unnecessary.**
  Reported build-time count: **148 warnings, 0 errors**, with the largest category being unused `React` identifiers (`import React from "react"` in modules that never name `React`), unused variables, and **4 unused `eslint-disable` directives**. Confirmed pattern in the reachable code: `src/pages/MobileSearch.jsx:1` `import React, { useState, useEffect } from 'react'` and `src/pages/Home.jsx:14` `import React, { useState, useEffect } from "react"` — the default import is never referenced. `eslint.config.js` (1,641 B) does not enable `react/jsx-uses-react` rules that would flag these as errors.
  Fix: enable the transform-aware rules so this is caught at lint time rather than noticed by hand:
  ```js
  rules: { "react/jsx-uses-react": "off", "react/react-in-jsx-scope": "off",
           "no-unused-vars": ["error", { argsIgnorePattern: "^_" }] }
  ```
  then codemod `import React, {` → `import {`.

---

## Summary

| severity | count |
|---|---:|
| P0 | 2 |
| P1 | 10 |
| P2 | 27 |
| P3 | 5 |
| **total** | **44** |

Highest-leverage fixes, in order:

1. **Delete the 17 dead modules + 5 dead auth modules and their CSS.** Removes ~2,300 lines of source, 313 lines of `index.css`, the entire undefined `--text-*/--bg-*` token system, and the Netflix-red paste-ins. Zero risk — nothing imports them.
2. **Fix `manualChunks` and lazy-load the player/detail routes.** Restores a cacheable 140 KB React chunk and pulls ~190 KB off the critical path. Verified broken today: `vendor-react` is 0 KB.
3. **Restore the type scale and the `bone` ramp in `tailwind.config.mjs`.** Fixes AA contrast for ~255 failing instances at the config level and makes the 286 arbitrary `text-[Npx]` codemod a find-and-replace instead of a redesign.
4. **Add static OG/Twitter tags + a PNG default `og:image`, and move `<meta charset>` to line 4.** Unblocks social unfurls entirely (currently zero) and future-proofs the decode path.
5. **Align the `/api/` service-worker TTL with `apiCache`.** Today `clearJSONCache()` on resume is silently defeated by a 24-hour SW cache, which is why content refresh appears not to work.
