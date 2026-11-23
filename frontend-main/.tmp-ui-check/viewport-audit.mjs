import { chromium, devices } from "file:///C:/Users/Ravikiran/AppData/Local/npm-cache/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs";
import fs from "node:fs";

const BASE = "https://cloudxmovies.vercel.app";
const OUT = "C:/Users/Ravikiran/Downloads/filmsclub/frontend-main/.tmp-ui-check";
const viewports = [
  { name: "320x568", width: 320, height: 568, mobile: true },
  { name: "360x740", width: 360, height: 740, mobile: true },
  { name: "390x844", width: 390, height: 844, mobile: true },
  { name: "412x915", width: 412, height: 915, mobile: true },
  { name: "844x390-landscape", width: 844, height: 390, mobile: true },
  { name: "768x1024-tablet", width: 768, height: 1024, mobile: true },
];
const results = [];

const browser = await chromium.launch({ headless: true });
for (const vp of viewports) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 2,
    isMobile: vp.mobile,
    hasTouch: vp.mobile,
    userAgent: vp.mobile
      ? "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
      : undefined,
  });
  const page = await context.newPage();
  const logs = [];
  page.on("console", (m) => { if (m.type() === "error") logs.push(m.text()); });
  page.on("pageerror", (e) => logs.push(`PAGEERROR ${e.message}`));
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(4500);

  // Dismiss the iOS install sheet if it appeared.
  const gotIt = page.getByRole("button", { name: "Got it" });
  if (await gotIt.isVisible().catch(() => false)) await gotIt.click();
  const closeInstall = page.getByRole("button", { name: "Dismiss install prompt" });
  if (await closeInstall.isVisible().catch(() => false)) await closeInstall.click();
  await page.waitForTimeout(600);

  const measure = async (label) => page.evaluate((label) => {
    const overflow = [];
    for (const el of document.querySelectorAll("body *")) {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      if (r.width <= 0 || s.position === "fixed") continue;
      // Ignore elements inside swiper's off-screen slides.
      if (el.closest(".swiper-wrapper")) continue;
      if (r.right > window.innerWidth + 1 || r.left < -1) {
        overflow.push({ tag: el.tagName, cls: String(el.className).slice(0, 90), l: Math.round(r.left), r: Math.round(r.right), w: Math.round(r.width) });
      }
    }
    const dock = document.querySelector(".mob-dock");
    const header = document.querySelector("body > div > div");
    return {
      label,
      path: location.pathname,
      scrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth,
      scrollH: document.documentElement.scrollHeight,
      dock: dock ? { top: Math.round(dock.getBoundingClientRect().top), bottom: Math.round(dock.getBoundingClientRect().bottom), h: Math.round(dock.getBoundingClientRect().height) } : null,
      header: header ? { h: Math.round(header.getBoundingClientRect().height) } : null,
      overflow: overflow.slice(0, 12),
      text: document.body.innerText.slice(0, 160).replace(/\n/g, " | "),
    };
  }, label);

  const home = await measure("home");
  await page.screenshot({ path: `${OUT}/vp-${vp.name}-home.png` });

  // Click the first card link into a details page.
  const card = page.locator('a[href^="/mov/"]').first();
  let detail = null;
  try {
    await card.click({ timeout: 8000 });
    await page.waitForTimeout(6000);
    const gotIt2 = page.getByRole("button", { name: "Got it" });
    if (await gotIt2.isVisible().catch(() => false)) await gotIt2.click();
    detail = await measure("detail");
    await page.screenshot({ path: `${OUT}/vp-${vp.name}-detail.png` });
  } catch (e) {
    logs.push(`detail click failed: ${e.message.slice(0, 160)}`);
  }

  results.push({ vp: vp.name, home, detail, logs });
  await context.close();
}
await browser.close();
fs.writeFileSync(`${OUT}/viewport-audit.json`, JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
