// Headless-Chromium driver for tests/test_results_photos_e2e.py.
//
// Reads {url, files: [{path, name, type?}], break?: [...]} as JSON on stdin,
// uploads the files under those names on an emulated Android phone, and prints
// JSON describing the results screen twice: fresh, then after a reload
// (restored from sessionStorage). If the app shows a message screen instead of
// results, `fresh` is {message} and there is no reload. Prints {"skip": reason}
// if Chromium is not installed.
//
// `break` makes the page's image decoders fail the way they have on a real
// Android phone: "createImageBitmap" rejects every photo, and "imgDecode"
// makes HTMLImageElement.decode() reject too (an <img> still displays).
//
// `redeploy: {dist}` simulates a deploy while the app stays open, as an
// installed PWA does on Android: after the results, it points the served
// index.html at a new bundle name, brings the page "back to the front", taps
// New batch, then deploys again and brings the upload screen back to the front.
// It reports which bundle the page was running at each step.
//
// `viewer: true` exercises the full-screen viewer on the fresh result with real
// touch input (Chromium DevTools touch events): open from №1, swipe through
// every photo, pinch to zoom, arrow keys, Escape, open from a runner-up and a
// left-out thumbnail, swipe down to close; then checks nothing opens on the
// restored result.

import { createRequire } from "node:module";
import fs from "node:fs";

const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { chromium, devices } = require("playwright");

const spec = JSON.parse(fs.readFileSync(0, "utf8"));

let browser;
try {
  browser = await chromium.launch();
} catch (err) {
  console.log(JSON.stringify({ skip: String(err.message).split("\n")[0] }));
  process.exit(0);
}

const context = await browser.newContext({ ...devices["Pixel 7"] });
const broken = new Set(spec.break ?? []);
if (broken.has("createImageBitmap")) {
  await context.addInitScript(() => {
    window.createImageBitmap = () =>
      Promise.reject(new DOMException("The source image could not be decoded.", "InvalidStateError"));
  });
}
if (broken.has("imgDecode")) {
  await context.addInitScript(() => {
    HTMLImageElement.prototype.decode = () =>
      Promise.reject(new DOMException("The source image cannot be decoded.", "EncodingError"));
  });
}
const page = await context.newPage();
const errors = [];
page.on("pageerror", (err) => errors.push(err.message));

// The /rank response, the mode the server chose (burst or set), and how many
// times /rank was called.
let mode = null;
let response = null;
let rankRequests = 0;
page.on("request", (req) => {
  if (req.method() === "POST" && new URL(req.url()).pathname === "/rank") rankRequests += 1;
});
page.on("response", async (res) => {
  if (res.request().method() === "POST" && new URL(res.url()).pathname === "/rank") {
    response = await res.json().catch(() => null);
    mode = response?.mode ?? `status ${res.status()}`;
  }
});

// Each result card: its photo_id, what it shows (the photo with its pixel
// width, the gradient placeholder, or a broken image) and its note.
async function readResults() {
  await page.waitForSelector(".hero-card, .message-screen", { timeout: 60_000 });
  if (await page.locator(".message-screen").count()) {
    return {
      message: await page.locator(".message-screen").evaluate((m) => ({
        eyebrow: m.querySelector(".ms-eyebrow").textContent,
        title: m.querySelector(".ms-title").textContent,
        body: m.querySelector(".ms-body").textContent,
      })),
    };
  }
  // Every image loads, or fails and is swapped for the placeholder. A broken
  // image that stays on screen times out here and is reported as "broken".
  await page
    .waitForFunction(
      () =>
        [...document.querySelectorAll(".hero-card img, .rank-card img")].every(
          (i) => i.complete && i.naturalWidth > 0,
        ),
      null,
      { timeout: 10_000 },
    )
    .catch(() => {});
  return page.evaluate(() => {
    const shows = (img) => (!img ? "placeholder" : img.naturalWidth > 0 ? "photo" : "broken");
    const hero = document.querySelector(".hero-card");
    const heroImg = hero.querySelector("img.hero-img");
    const cards = [{
      id: hero.querySelector(".placeholder-tag").textContent,
      shows: shows(heroImg),
      width: heroImg?.naturalWidth ?? null,
      note: hero.querySelector(".hero-note").firstChild.textContent,
    }];
    for (const card of document.querySelectorAll(".rank-card:not(.left-out-card)")) {
      const img = card.querySelector(".rank-thumb img");
      cards.push({
        id: card.querySelector(".rank-sub").textContent,
        shows: shows(img),
        width: img?.naturalWidth ?? null,
        note: card.querySelector(".rank-blurb").textContent,
        flag: card.querySelector(".rank-flag")?.textContent ?? null,
      });
    }
    const save = document.querySelector(".results-footer .btn-primary");
    const style = getComputedStyle(save);
    const leftOut = [...document.querySelectorAll(".left-out-card")].map((card) => {
      const img = card.querySelector(".rank-thumb img");
      return {
        id: card.querySelector(".rank-sub").textContent,
        reason: card.querySelector(".rank-blurb").textContent,
        shows: shows(img),
      };
    });
    return {
      cards,
      leftOut,
      meta: document.querySelector(".results-meta").textContent,
      explainer: document.querySelector(".burst-explainer")?.textContent.trim() ?? null,
      switcherShown: document.querySelector(".rerank") !== null,
      openers: document.querySelectorAll(".photo-open, .thumb-open").length,
      rankedBy: document.querySelector(".hero-card .label-main").textContent,
      saveEnabled: !save.disabled,
      saveLook: { opacity: Number(style.opacity), cursor: style.cursor },
      notice: document.querySelector(".prep-notice")?.textContent ?? null,
    };
  });
}

await page.goto(spec.url);
await page.setInputFiles(
  'input[type="file"]',
  spec.files.map((f) => ({
    name: f.name, mimeType: f.type ?? "image/jpeg", buffer: fs.readFileSync(f.path),
  })),
);
await page.getByRole("button", { name: `Rank ${spec.files.length} photos` }).click();
const fresh = await readResults();
if (fresh.message) {
  console.log(JSON.stringify({ mode, response, rankRequests, fresh, switcher: null, restored: null, errors }));
  await browser.close();
  process.exit(0);
}

// Profile switcher (set mode only): where the hero card sits under each
// profile, whether the reset link shows, and whether any note appeared.
let switcher = null;
if (await page.locator(".rerank").count()) {
  switcher = { heroTop: {}, reset: {}, notes: 0 };
  for (const chip of await page.locator(".rerank-chip").all()) {
    await chip.click();
    const name = (await chip.textContent()).trim();
    switcher.heroTop[name] = await page.locator(".hero-card").evaluate((e) => e.offsetTop);
    switcher.reset[name] = await page.locator(".rerank-reset").count() > 0;
    switcher.notes += await page.locator(".rerank-note").count();
  }
}

// The full-screen viewer (see the header).
let viewer = null;
if (spec.viewer) {
  const cdp = await context.newCDPSession(page);
  const touch = (type, points) =>
    cdp.send("Input.dispatchTouchEvent", {
      type, touchPoints: points.map(([x, y], id) => ({ x, y, id })),
    });
  const gesture = async (from, to) => {
    await touch("touchStart", from);
    for (let i = 1; i <= 10; i++) {
      await touch("touchMove", from.map(([x, y], k) => [
        x + ((to[k][0] - x) * i) / 10, y + ((to[k][1] - y) * i) / 10,
      ]));
    }
    await touch("touchEnd", []);
    await page.waitForTimeout(150);
  };
  const { width: W, height: H } = page.viewportSize();
  const state = () => page.evaluate(() => {
    const v = document.querySelector(".viewer");
    if (!v) return null;
    const img = v.querySelector(".viewer-img");
    return {
      index: Number(v.dataset.index),
      title: v.querySelector(".viewer-title").textContent,
      name: v.querySelector(".viewer-name").textContent,
      flag: v.querySelector(".viewer-flag")?.textContent ?? null,
      count: v.querySelector(".viewer-count").textContent.trim(),
      scale: Number(v.querySelector(".viewer-stage").dataset.scale),
      width: img?.naturalWidth ?? null,
      focus: document.activeElement?.getAttribute("aria-label") ?? null,
    };
  });
  const loaded = () => page.waitForFunction(() => {
    const img = document.querySelector(".viewer-img");
    return !img || (img.complete && img.naturalWidth > 0);
  });

  viewer = { slides: [] };
  // The order on screen now (the profile switcher may have re-ranked it).
  viewer.order = await page.evaluate(() => [
    document.querySelector(".hero-card .placeholder-tag").textContent,
    ...[...document.querySelectorAll(".rank-card:not(.left-out-card) .rank-sub")].map((e) => e.textContent),
    ...[...document.querySelectorAll(".left-out-card .rank-sub")].map((e) => e.textContent),
  ]);
  await page.locator(".hero-card .photo-open").click();
  await page.waitForSelector(".viewer");
  await loaded();
  viewer.slides.push(await state());
  for (;;) {
    const before = (await state()).index;
    await gesture([[W * 0.8, H / 2]], [[W * 0.15, H / 2]]); // swipe left: next
    await loaded();
    const now = await state();
    if (now.index === before) break;
    viewer.slides.push(now);
  }
  await gesture([[W / 2 - 30, H / 2], [W / 2 + 30, H / 2]], [[W / 2 - 140, H / 2], [W / 2 + 140, H / 2]]);
  viewer.pinched = (await state()).scale;
  await page.keyboard.press("ArrowLeft");
  viewer.afterArrowLeft = await state();
  await page.keyboard.press("Escape");
  viewer.afterEscape = await state();

  await page.locator(".rank-card:not(.left-out-card) .thumb-open").first().click();
  await page.waitForSelector(".viewer");
  viewer.fromRunnerUp = await state();
  await gesture([[W / 2, H * 0.3]], [[W / 2, H * 0.8]]); // swipe down: close
  viewer.afterSwipeDown = await state();

  if (await page.locator(".left-out-card .thumb-open").count()) {
    await page.locator(".left-out-card .thumb-open").first().click();
    await page.waitForSelector(".viewer");
    viewer.fromLeftOut = await state();
    await page.locator(".viewer-close").click();
    viewer.afterClose = await state();
  }
}

await page.reload();
const restored = await readResults();
if (viewer) {
  await page.locator(".hero-card .hero-photo").click();
  await page.waitForTimeout(300);
  viewer.restoredOpens = (await page.locator(".viewer").count()) > 0;
}

// A deploy while the app stays open (see the header).
let redeploy = null;
if (spec.redeploy) {
  const path = await import("node:path");
  const dist = spec.redeploy.dist;
  const deploy = (tag) => {
    const index = path.join(dist, "index.html");
    const html = fs.readFileSync(index, "utf8");
    const current = html.match(/\/assets\/(index-[\w-]+\.js)/)[1];
    const next = `index-${tag}.js`;
    fs.copyFileSync(path.join(dist, "assets", current), path.join(dist, "assets", next));
    fs.writeFileSync(index, html.replace(current, next));
    return `/assets/${next}`;
  };
  const running = () =>
    page.evaluate(() => new URL(document.querySelector('script[type="module"][src*="/assets/"]').src).pathname);
  const backToFront = () => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));

  redeploy = { first: deploy("redeployed1") };
  await backToFront();
  await page.waitForTimeout(1500);
  redeploy.onResults = {
    running: await running(),
    stillShowsResults: (await page.locator(".hero-card").count()) > 0,
  };
  const reloaded = page.waitForEvent("load", { timeout: 15_000 }).catch(() => null);
  await page.getByRole("button", { name: /New batch/ }).click();
  await reloaded;
  await page.waitForSelector(".upload-cta");
  redeploy.afterNewBatch = await running();

  redeploy.second = deploy("redeployed2");
  const reloadedAgain = page.waitForEvent("load", { timeout: 15_000 }).catch(() => null);
  await backToFront();
  await reloadedAgain;
  await page.waitForSelector(".upload-cta");
  redeploy.afterResume = await running();
}

console.log(JSON.stringify({ mode, response, rankRequests, fresh, switcher, restored, redeploy, viewer, errors }));
await browser.close();
