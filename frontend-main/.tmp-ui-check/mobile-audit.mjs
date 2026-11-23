import { chromium } from "file:///C:/Users/Ravikiran/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";
import fs from "node:fs";

const URL = process.env.AUDIT_URL || "http://127.0.0.1:4173/";
const OUT = "C:/Users/Ravikiran/Downloads/filmsclub/frontend-main/.tmp-ui-check";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
  userAgent:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
});
const page = await context.newPage();
const logs = [];
page.on("console", (m) => logs.push(`[console:${m.type()}] ${m.text()}`));
page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}\n${e.stack || ""}`));
page.on("requestfailed", (r) => logs.push(`[requestfailed] ${r.url()} :: ${r.failure()?.errorText}`));

const started = Date.now();
try {
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 45000 });
} catch (e) {
  logs.push(`[goto] ${e.message}`);
}
await page.waitForTimeout(9000);

const audit = await page.evaluate(() => {
  const root = document.getElementById("root");
  const main = document.getElementById("main-content");
  const nav = document.querySelector(".mob-dock");
  const hero = document.querySelector(".film-grain");
  const cs = getComputedStyle(document.body);
  const rect = (el) => (el ? el.getBoundingClientRect().toJSON() : null);
  const overflowers = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1)) {
      const s = getComputedStyle(el);
      if (s.position !== "fixed" && s.position !== "absolute") {
        overflowers.push({
          tag: el.tagName,
          cls: String(el.className).slice(0, 120),
          left: Math.round(r.left),
          right: Math.round(r.right),
          width: Math.round(r.width),
        });
      }
    }
  }
  return {
    title: document.title,
    readyState: document.readyState,
    rootChildren: root ? root.children.length : -1,
    bootShell: Boolean(document.getElementById("boot-shell")),
    bodyBg: cs.backgroundColor,
    bodyColor: cs.color,
    htmlScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    visualViewport: window.visualViewport
      ? { w: window.visualViewport.width, h: window.visualViewport.height }
      : null,
    main: rect(main),
    nav: rect(nav),
    hero: rect(hero),
    dockText: nav ? nav.innerText.replace(/\n/g, "|") : null,
    visibleText: document.body.innerText.slice(0, 800),
    overflowers: overflowers.slice(0, 25),
    imgCount: document.images.length,
    brokenImgs: [...document.images]
      .filter((i) => i.complete && i.naturalWidth === 0)
      .map((i) => i.currentSrc || i.src)
      .slice(0, 10),
    sw: "serviceWorker" in navigator ? navigator.serviceWorker.controller?.scriptURL || "no-controller" : "unsupported",
  };
});

await page.screenshot({ path: `${OUT}/mobile-home.png`, fullPage: false });
await page.screenshot({ path: `${OUT}/mobile-home-full.png`, fullPage: true });
fs.writeFileSync(`${OUT}/mobile-audit.json`, JSON.stringify({ elapsed: Date.now() - started, audit, logs }, null, 2));
console.log(JSON.stringify({ elapsed: Date.now() - started, audit, logs }, null, 2));
await browser.close();
