// Headless-Chromium driver for tests/test_results_photos_e2e.py.
//
// Reads {url, files: [{path, name}]} as JSON on stdin, uploads the files under
// those names on an emulated Android phone, and prints JSON describing the
// results screen twice: fresh, then after a reload (restored from
// sessionStorage). Prints {"skip": reason} if Chromium is not installed.

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

const page = await (await browser.newContext({ ...devices["Pixel 7"] })).newPage();
const errors = [];
page.on("pageerror", (err) => errors.push(err.message));

// Each result card: its photo_id and what it shows (the photo with its pixel
// width, or the gradient placeholder).
async function readResults() {
  await page.waitForSelector(".hero-card", { timeout: 60_000 });
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".hero-card img, .rank-card img")].every((i) => i.complete),
  );
  return page.evaluate(() => {
    const shows = (img) => (!img ? "placeholder" : img.naturalWidth > 0 ? "photo" : "broken");
    const hero = document.querySelector(".hero-card");
    const heroImg = hero.querySelector("img.hero-img");
    const cards = [{
      id: hero.querySelector(".placeholder-tag").textContent,
      shows: shows(heroImg),
      width: heroImg?.naturalWidth ?? null,
    }];
    for (const card of document.querySelectorAll(".rank-card")) {
      const img = card.querySelector(".rank-thumb img");
      cards.push({
        id: card.querySelector(".rank-sub").textContent,
        shows: shows(img),
        width: img?.naturalWidth ?? null,
      });
    }
    const save = document.querySelector(".results-footer .btn-primary");
    return { cards, saveEnabled: !save.disabled };
  });
}

await page.goto(spec.url);
await page.setInputFiles(
  'input[type="file"]',
  spec.files.map((f) => ({ name: f.name, mimeType: "image/jpeg", buffer: fs.readFileSync(f.path) })),
);
await page.getByRole("button", { name: `Rank ${spec.files.length} photos` }).click();
const fresh = await readResults();

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

console.log(JSON.stringify({ fresh, switcher, restored, errors }));
await browser.close();
