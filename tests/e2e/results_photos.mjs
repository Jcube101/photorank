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
    for (const card of document.querySelectorAll(".rank-card")) {
      const img = card.querySelector(".rank-thumb img");
      cards.push({
        id: card.querySelector(".rank-sub").textContent,
        shows: shows(img),
        width: img?.naturalWidth ?? null,
        note: card.querySelector(".rank-blurb").textContent,
      });
    }
    const save = document.querySelector(".results-footer .btn-primary");
    const style = getComputedStyle(save);
    return {
      cards,
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

await page.reload();
const restored = await readResults();

console.log(JSON.stringify({ mode, response, rankRequests, fresh, switcher, restored, errors }));
await browser.close();
