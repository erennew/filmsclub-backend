import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import process from "node:process";

const DAY = 60 * 60 * 24;

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      // "prompt" keeps installed clients on the current build until the user
      // accepts the update toast in PWAInstallPrompt.
      registerType: "prompt",
      injectRegister: null,
      devOptions: { enabled: false },
      includeAssets: [],
      manifest: {
        id: "/",
        name: "CloudXMovies — Late-Night Screening Room",
        short_name: "CloudXMovies",
        description:
          "Stream movies, series, anime and K-Dramas in HD — a late-night screening room with no account, no noise, just the film.",
        lang: "en",
        dir: "ltr",
        start_url: "/",
        scope: "/",
        display: "standalone",
        display_override: ["standalone", "minimal-ui"],
        orientation: "any",
        background_color: "#08090B",
        theme_color: "#08090B",
        categories: ["entertainment", "video", "movies"],
        prefer_related_applications: false,
        launch_handler: { client_mode: "navigate-existing" },
        icons: [
          {
            src: "/icons/icon-192x192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/icon-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/maskable-icon-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
        screenshots: [
          {
            src: "/screenshots/home-narrow.png",
            sizes: "1080x1920",
            type: "image/png",
            form_factor: "narrow",
            label: "Tonight's feature on the mobile shelf",
          },
          {
            src: "/screenshots/home-wide.png",
            sizes: "1920x1080",
            type: "image/png",
            form_factor: "wide",
            label: "The screening room on desktop",
          },
        ],
        shortcuts: [
          {
            name: "Movies",
            short_name: "Movies",
            description: "Browse the film library",
            url: "/Movies",
            icons: [{ src: "/icons/shortcut-movies.png", sizes: "96x96", type: "image/png" }],
          },
          {
            name: "Series",
            short_name: "Series",
            description: "Browse every series",
            url: "/Series",
            icons: [{ src: "/icons/shortcut-series.png", sizes: "96x96", type: "image/png" }],
          },
          {
            name: "Anime",
            short_name: "Anime",
            description: "Browse anime",
            url: "/anime",
            icons: [{ src: "/icons/shortcut-anime.png", sizes: "96x96", type: "image/png" }],
          },
          {
            name: "K-Drama",
            short_name: "K-Drama",
            description: "Browse K-Dramas",
            url: "/k-drama",
            icons: [{ src: "/icons/shortcut-kdrama.png", sizes: "96x96", type: "image/png" }],
          },
        ],
      },
      workbox: {
        globPatterns: [
          "**/*.{js,css,html,svg,png,jpg,jpeg,webp,avif,ico,woff2,txt}",
        ],
        globIgnores: [
          "**/cloudxmovies-logo.svg",
          "**/oldcloudx.svg",
          "**/vite.svg",
          "**/icons/icon-*.svg",
          "**/splash/**",
          "**/screenshots/**",
        ],
        navigateFallback: "/index.html",
        // /admin must come from the network: a precached index.html would pin an
        // installed PWA to the pre-admin bundle and 404 the console until it updates.
        navigateFallbackDenylist: [/^\/api\//, /^\/dl\//, /^\/admin/],
        cleanupOutdatedCaches: true,
        clientsClaim: true,
        skipWaiting: false,
        maximumFileSizeToCacheInBytes: 6 * 1024 * 1024,
        runtimeCaching: [
          {
            /*
             * Playback URLs (/dl/...) and file metadata expire server-side, so a
             * 24h cache handed viewers dead links that only recovered by clearing
             * site data. Five minutes keeps offline-ish browsing useful without
             * serving expired streams. The queue/hero/config admin endpoints are
             * excluded entirely — they must never be served from cache.
             */
            urlPattern: ({ url, request }) => {
              if (!url.pathname.startsWith("/api/")) return false;
              if (request.method !== "GET") return false;
              if (url.pathname.startsWith("/api/stream/")) return false;
              if (url.pathname.startsWith("/api/dl/")) return false;
              return true;
            },
            handler: "NetworkFirst",
            method: "GET",
            options: {
              cacheName: "cloudx-api",
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 120, maxAgeSeconds: 5 * 60 },
              cacheableResponse: { statuses: [200] },
            },
          },
          {
            urlPattern: ({ request }) => request.destination === "image",
            handler: "CacheFirst",
            options: {
              cacheName: "cloudx-images",
              expiration: { maxEntries: 300, maxAgeSeconds: 30 * DAY },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "google-font-stylesheets",
              expiration: { maxEntries: 10, maxAgeSeconds: 365 * DAY },
            },
          },
          {
            urlPattern: /^https:\/\/fonts\.gstatic\.com\/.*/i,
            handler: "CacheFirst",
            options: {
              cacheName: "google-fonts",
              expiration: { maxEntries: 30, maxAgeSeconds: 365 * DAY },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],

  build: {
    // Target modern browsers for better code output
    target: "es2020",
    // Enable CSS code splitting
    cssCodeSplit: true,
    // Source maps for production debugging
    sourcemap: false,
    // Chunk size warning
    chunkSizeWarningLimit: 1200,

    rollupOptions: {
      output: {
        /*
         * Manual chunk splitting for optimal caching.
         *
         * This MUST be the resolver-function form, not the object form. With the
         * object form Rollup resolves the listed packages but React still ends up
         * inlined into the entry chunk (the emitted `vendor-react` was 0 bytes and
         * was missing from the modulepreload list), which meant every deploy
         * invalidated React, ReactDOM and all app code under one hash.
         * Splitting by resolved module id is the reliable form under Vite 5.
         */
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;

          // Normalise Windows separators so the matched groups stay stable.
          const modPath = id.split("\\").join("/");

          // React core + scheduler — cached long-term, must not land in the entry.
          if (/\/node_modules\/(react|react-dom|scheduler)\//.test(modPath)) {
            return "vendor-react";
          }
          if (/\/node_modules\/(react-router|react-router-dom|@remix-run\/router)\//.test(modPath)) {
            return "vendor-router";
          }
          if (/\/node_modules\/framer-motion\//.test(modPath)) return "vendor-motion";
          if (/\/node_modules\/swiper\//.test(modPath)) return "vendor-swiper";
          if (/\/node_modules\/react-icons\//.test(modPath)) return "vendor-icons";
          /*
           * NextUI + its react-aria/react-stately runtime are used by exactly one
           * lazy route (the details page, via DownloadButton/TelegramButton) and
           * by nothing on the homepage or any list page.
           *
           * These rules used to pin them to a shared "vendor-nextui" chunk. Rollup
           * then had to keep that chunk in the *static* import graph, so every
           * route chunk — Home included — emitted `import"./vendor-nextui-*.js"`
           * and index.html added a modulepreload for all 237 KB. Measured: 568 KB
           * of unconditional modulepreload on every page.
           *
           * Returning undefined lets Rollup place these modules where they are
           * actually reached, so the chunk follows the details route and is only
           * fetched when that route loads.
           */
          if (/\/node_modules\/@nextui-org\//.test(modPath)) return undefined;
          if (/\/node_modules\/@react-aria\//.test(modPath)) return undefined;
          if (/\/node_modules\/@react-stately\//.test(modPath)) return undefined;
          if (/\/node_modules\/react-lazy-load-image-component\//.test(modPath)) return "vendor-lazy";
          if (/\/node_modules\/axios\//.test(modPath)) return "vendor-axios";
          if (/\/node_modules\/(plyr|plyr-react)\//.test(modPath)) return "vendor-player";
          if (/\/node_modules\/lottie-react\//.test(modPath)) return "vendor-lottie";

          return undefined;
        },
        // Consistent file naming
        chunkFileNames: "assets/js/[name]-[hash].js",
        entryFileNames: "assets/js/[name]-[hash].js",
        assetFileNames: (assetInfo) => {
          const info = assetInfo.name?.split(".") || [];
          const ext = info[info.length - 1];
          if (/png|jpe?g|svg|gif|webp|avif/.test(ext)) return "assets/img/[name]-[hash][extname]";
          if (/woff2?|eot|ttf|otf/.test(ext)) return "assets/fonts/[name]-[hash][extname]";
          if (ext === "css") return "assets/css/[name]-[hash][extname]";
          return "assets/[name]-[hash][extname]";
        },
      },
    },
  },

  // Optimize dependencies
  optimizeDeps: {
    include: [
      "react",
      "react-dom",
      "react-router-dom",
      "framer-motion",
      "axios",
      "swiper",
      "swiper/react",
    ],
  },

  server: {
    port: 5173,
    open: false,
    hmr: { overlay: true },
  },

  // Enable GZIP compression hints
  esbuild: {
    legalComments: "none",
    target: "es2020",
    drop: process.env.NODE_ENV === "production" ? ["console", "debugger"] : [],
  },
});
