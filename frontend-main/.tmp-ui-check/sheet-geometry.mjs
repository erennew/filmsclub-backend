import { chromium } from "file:///C:/Users/Ravikiran/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.AUDIT_URL || "http://127.0.0.1:4173";
const IPHONE_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

const browser = await chromium.launch({ headless: true });
const out = [];
for (const vp of [
  { name: "320x568", width: 320, height: 568 },
  { name: "360x740", width: 360, height: 740 },
  { name: "390x844", width: 390, height: 844 },
  { name: "844x390-landscape", width: 844, height: 390 },
]) {
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 2, isMobile: true, hasTouch: true, userAgent: IPHONE_UA,
  });
  const page = await ctx.newPage();
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 45000 });
  await page.waitForTimeout(11500);
  const r = await page.evaluate(() => {
    const main = document.getElementById("main-content");
    const heroWrap = main?.firstElementChild;
    const dialog = document.querySelector('[role="dialog"]');
    const dock = document.querySelector(".mob-dock");
    const box = (el) => (el ? { t: Math.round(el.getBoundingClientRect().top), b: Math.round(el.getBoundingClientRect().bottom), h: Math.round(el.getBoundingClientRect().height) } : null);
    // How much of the first screen does the sheet occupy?
    const d = dialog?.getBoundingClientRect();
    const coverage = d ? Math.round(((Math.min(d.bottom, window.innerHeight) - Math.max(d.top, 0)) / window.innerHeight) * 100) : 0;
    return {
      vp: `${window.innerWidth}x${window.innerHeight}`,
      heroArea: box(heroWrap),
      heroFirstChildText: heroWrap?.innerText?.slice(0, 60).replace(/\n/g, " | "),
      dialog: box(dialog),
      dialogViewportCoveragePct: coverage,
      dock: box(dock),
      // Is the primary hero CTA clickable (not under the sheet)?
      ctaBlocked: (() => {
        const cta = [...document.querySelectorAll("a,button")].find((el) => /watch now|try again|browse movies/i.test(el.innerText || ""));
        if (!cta || !d) return null;
        const c = cta.getBoundingClientRect();
        return !(c.bottom < d.top || c.top > d.bottom || c.right < d.left || c.left > d.right);
      })(),
    };
  });
  out.push(r);
  await ctx.close();
}
await browser.close();
fs.writeFileSync("C:/Users/Ravikiran/Downloads/filmsclub/frontend-main/.tmp-ui-check/sheet-geometry.json", JSON.stringify(out, null, 2));
console.log(JSON.stringify(out, null, 2));
