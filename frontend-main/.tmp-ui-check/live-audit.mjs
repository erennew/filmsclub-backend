import { chromium, webkit, devices } from "file:///C:/Users/Ravikiran/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = process.env.AUDIT_URL || "https://cloudxmovies.vercel.app";
const OUT = "C:/Users/Ravikiran/Downloads/filmsclub/frontend-main/.tmp-ui-check";
const routes = ["/", "/Movies", "/Series", "/mov/1368337"];
const results = [];

const engine = process.env.ENGINE === "webkit" ? webkit : chromium;

for (const route of routes) {
  const browser = await engine.launch({ headless: true });
  const context = await browser.newContext({
    ...devices["iPhone 13"],
  });
  const page = await context.newPage();
  const logs = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") logs.push(`[${m.type()}] ${m.text()}`);
  });
  page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));
  page.on("requestfailed", (r) => logs.push(`[reqfail] ${r.url().slice(0, 160)} :: ${r.failure()?.errorText}`));

  let status = null;
  try {
    const resp = await page.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 60000 });
    status = resp?.status();
  } catch (e) {
    logs.push(`[goto] ${e.message}`);
  }
  // Let network settle, but cap it.
  try { await page.waitForLoadState("networkidle", { timeout: 25000 }); } catch {}
  await page.waitForTimeout(3500);

  const audit = await page.evaluate(() => {
    const all = [...document.querySelectorAll("body *")];
    const overflowers = [];
    for (const el of all) {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      if (r.width > 0 && (r.right > window.innerWidth + 1 || r.left < -1) && s.position !== "fixed") {
        overflowers.push({
          tag: el.tagName,
          cls: String(el.className).slice(0, 100),
          left: Math.round(r.left),
          right: Math.round(r.right),
          w: Math.round(r.width),
        });
      }
    }
    const dock = document.querySelector(".mob-dock");
    const nav = document.querySelector("body > div > div, header");
    return {
      title: document.title,
      rootChildren: document.getElementById("root")?.children.length,
      bootShell: Boolean(document.getElementById("boot-shell")),
      bodyBg: getComputedStyle(document.body).backgroundColor,
      scrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
      scrollH: document.documentElement.scrollHeight,
      dock: dock ? dock.getBoundingClientRect().toJSON() : null,
      imgs: document.images.length,
      broken: [...document.images].filter((i) => i.complete && i.naturalWidth === 0).map((i) => (i.currentSrc || i.src).slice(0, 120)).slice(0, 8),
      overflowers: overflowers.slice(0, 30),
      text: document.body.innerText.slice(0, 400),
      sw: navigator.serviceWorker?.controller?.scriptURL || "none",
      fcp: performance.getEntriesByName("first-contentful-paint")[0]?.startTime ?? null,
      lcp: performance.getEntriesByType("largest-contentful-paint").slice(-1)[0]?.startTime ?? null,
      dcl: performance.getEntriesByType("navigation")[0]?.domContentLoadedEventEnd ?? null,
      load: performance.getEntriesByType("navigation")[0]?.loadEventEnd ?? null,
      transfer: performance.getEntriesByType("resource").reduce((n, r) => n + (r.transferSize || 0), 0),
      resources: performance.getEntriesByType("resource").length,
    };
  });

  const shot = `${OUT}/live-${engine.name()}-${route.replace(/[^\w]+/g, "_") || "home"}.png`;
  await page.screenshot({ path: shot, fullPage: false });
  results.push({ engine: engine.name(), route, status, shot, audit, logs });
  await browser.close();
}

fs.writeFileSync(`${OUT}/live-audit-${engine.name()}.json`, JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
