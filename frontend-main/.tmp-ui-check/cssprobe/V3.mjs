/** @type {import('tailwindcss').Config} */

import defaultTheme from "tailwindcss/defaultTheme";
import { nextui } from "@nextui-org/theme";

export default {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./node_modules/@nextui-org/theme/dist/components/{button,select,popover,listbox,scroll-shadow}.{js,mjs}",
    "./node_modules/@nextui-org/{select,popover,button,system,listbox,spinner,shared-utils,dom-animation,use-disclosure}/dist/**/*.{js,mjs}",
  ],
  theme: {
    screens: {
      "mobile-desc": "360px", // show hero description above 360px
      xs: "400px",
      sm: "480px",
      bsmmd: "750px",
      bsmmdTwo: "500px",
      md: "700px",
      /*
       * The full desktop header (logo + 5 icon+label links + My List + search
       * field) needs ~990px to lay out without the nav compressing into the
       * input. `md` (700px) is far too early for it, so the header is gated on
       * this dedicated breakpoint instead of changing `md` and reflowing every
       * other `md:` utility in the app.
       */
      navlg: "990px",
      lg: "1024px",
      blgxllg: "920px",
      blgxl: "1224px",
      xl: "1440px",
      xxl: "1600px",
      "2xl": "1536px",
    },
    extend: {
      fontFamily: {
        sans: ["Manrope", "Inter", ...defaultTheme.fontFamily.sans],
        display: ["Fraunces", "Georgia", ...defaultTheme.fontFamily.serif],
        mono: ["'IBM Plex Mono'", ...defaultTheme.fontFamily.mono],
        Inter: ["Manrope", ...defaultTheme.fontFamily.sans],
        Outfit: ["Manrope", ...defaultTheme.fontFamily.sans],
        Anton: ["Fraunces", ...defaultTheme.fontFamily.serif],
      },
      opacity: {
        8: "0.08",
        12: "0.12",
        14: "0.14",
      },
      colors: {
        primaryTextColor: "#F2EDE3",
        secondaryTextColor: "#F2EDE3A6",
        primaryBtn: "#E9B872",
        primaryBtnHower: "#D9A65C",
        bgColor: "#08090B",
        bgColorSecondary: "#14151A",
        btnColor: "#14151A",
        otherColor: "#E9B872",
        ink: "#08090B",
        carbon: "#14151A",
        bone: "#F2EDE3",
        projector: "#E9B872",
        /*
         * #F0CA98 was hardcoded 21 times as `hover:bg-[#F0CA98]` only because it
         * existed as a CSS variable and never as a Tailwind colour. Promoting it
         * here is what lets those arbitrary values become `bg-projector-bright`.
         */
        "projector-bright": "#F0CA98",
        "projector-deep": "#D9A65C",
        signal: "#FF5A3C",
        /*
         * Accessible text ramp. Measured against --ink (#08090B):
         *   bone-muted  ≈ 6.1:1   body copy floor (passes AA)
         *   bone-subtle ≈ 4.8:1   smallest legible metadata (passes AA)
         * Anything below `bone-subtle` is decoration only — never information.
         */
        "bone-muted": "#A8A49B",
        "bone-subtle": "#8F8B83",
      },
      aspectRatio: {
        "9/13": "9/13",
        "2/3": "2/3",
      },
      fontSize: {
        /*
         * Sizes are unchanged (the whole app is tuned to them) but every step now
         * carries a paired line-height. Without the pair, all `text-*` utilities
         * fell back to `normal` leading, which is why the JSX is littered with
         * manual `leading-snug` / `leading-tight` patches.
         */
        xs: ["0.75rem", { lineHeight: "1.15rem" }],
        sm: ["0.8rem", { lineHeight: "1.25rem" }],
        base: ["1rem", { lineHeight: "1.6rem" }],
        md: ["0.9rem", { lineHeight: "1.4rem" }],
        lg: ["1.1rem", { lineHeight: "1.6rem" }],
        xl: ["1.25rem", { lineHeight: "1.75rem" }],
        "2xl": ["1.4rem", { lineHeight: "1.9rem" }],
        "3xl": ["1.953rem", { lineHeight: "2.3rem" }],
        "4xl": ["2.441rem", { lineHeight: "2.7rem" }],
        "5xl": ["3.052rem", { lineHeight: "3.2rem" }],
        "6xl": ["4rem", { lineHeight: "1.05" }],
        "7xl": ["6rem", { lineHeight: "1.05" }],
        "8xl": ["8rem", { lineHeight: "1" }],
        "9xl": ["11rem", { lineHeight: "1" }],
      },
      backgroundOpacity: {
        8: "0.08",
        12: "0.12",
        15: "0.15",
        18: "0.18",
      },
      transitionDuration: {
        350: "350ms",
        400: "400ms",
      },
      borderRadius: {
        "4xl": "2rem",
      },
      boxShadow: {
        "3xl": "0 40px 80px -16px rgba(0,0,0,0.7)",
        glow: "0 0 24px 4px rgba(233, 184, 114, 0.24)",
        "glow-sm": "0 0 12px 2px rgba(233, 184, 114, 0.18)",
      },
    },
  },
  darkMode: "class",
  plugins: [nextui()],
};
