import { chromium } from "file:///C:/Users/Ravikiran/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.AUDIT_URL || "http://127.0.0.1:4173";
const OUT = "C:/Users/Ravikiran/Downloads/filmsclub/frontend-main/.tmp-ui-check";
const viewports = [
  { name: "320x568", width: 320, height: 568 },
  { name: "360x740", width: 360, height: 740 },
  { name: "390x844", width: 390, height: 844 },
  { name: "844x390-landscape", width: 844, height: 390 },
];
const routes = (process.env.ROUTES || "/,/Movies,/Series,/mov/1368337").split(",");
const results = [];

// iPhone UA => PWAInstallPrompt treats the run as iOS (auto-open path).
const IPHONE_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

const browser = await chromium.launch({ headless: true });
for (const vp of viewports) {
  for (const route of routes) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
      userAgent: IPHONE_UA,
    });
    const page = await context.newPage();
    const logs = [];
    page.on("console", (m) => {
      if (m.type() === "error") logs.push(m.text().slice(0, 140));
    });
    page.on("pageerror", (e) => logs.push(`pageerror ${e.message.slice(0, 140)}`));

    let status = null;
    try {
      const resp = await page.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 45000 });
      status = resp?.status();
    } catch (e) {
      logs.push(`goto ${e.message.slice(0, 120)}`);
    }
    // Wait past the 9s iOS auto-open timer so the sheet state is observed.
    await page.waitForTimeout(11500);

    const audit = await page.evaluate(() => {
      const vw = window.innerWidth;
      const overflow = [];
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        if (r.width <= 0) continue;
        if (s.position === "fixed") continue;
        if (el.closest(".swiper-wrapper")) continue;
        // Only real document overflow: element extends past the right edge
        // and is not inside a horizontally scrollable ancestor.
        let scroller = false;
        for (let p = el.parentElement; p; p = p.parentElement) {
          const ps = getComputedStyle(p);
          if (ps.overflowX === "auto" || ps.overflowX === "scroll") { scroller = true; break; }
        }
        if (scroller) continue;
        if (r.right > vw + 1 || r.left < -1) {
          overflow.push({
            tag: el.tagName,
            cls: String(el.className).slice(0, 80),
            l: Math.round(r.left),
            r: Math.round(r.right),
            w: Math.round(r.width),
          });
        }
      }
      // Install sheet / hero geometry.
      const dialog = document.querySelector('[role="dialog"]');
      const hero = document.querySelector(".swiper");
      const dock = document.querySelector(".mob-dock");
      const rect = (el) => (el ? { ...el.getBoundingClientRect().toJSON() } : null);
      return {
        scrollW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth,
        scrollH: document.documentElement.scrollHeight,
        dialog: rect(dialog),
        dialogOpen: Boolean(dialog),
        hero: rect(hero),
        dock: rect(dock),
        overflow: overflow.slice(0, 10),
        text: document.body.innerText.slice(0, 120).replace(/\n/g, " | "),
      };
    });

    const dialogCoversHero = Boolean(
      audit.dialogOpen &&
        audit.hero &&
        audit.dialog &&
        audit.dialog.top < audit.hero.bottom &&
        audit.dialog.bottom > audit.hero.top
    );

    results.push({ vp: vp.name, route, status, dialogOpen: audit.dialogOpen, dialogCoversHero, audit, logs });
    await page.screenshot({ path: `${OUT}/fit-${vp.name}-${route.replace(/[^\w]+/g, "_") || "home"}.png` });
    await context.close();
  }
}
await browser.close();
fs.writeFileSync(`${OUT}/local-fit.json`, JSON.stringify(results, null, 2));
for (const r of results) {
  console.log(
    `${r.vp.padEnd(20)} ${r.route.padEnd(16)} status=${r.status} scrollW=${r.audit.scrollW}/${r.audit.clientW} ` +
      `installsheet=${r.dialogOpen} coversHero=${r.dialogCoversHero} overflow=${r.audit.overflow.length}`
  );
  for (const o of r.audit.overflow) console.log(`    ${o.tag} l=${o.l} r=${o.r} w=${o.w} ${o.cls}`);
  for (const l of r.logs) console.log(`    log: ${l}`);
}
