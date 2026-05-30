// Generates PhotoRank PWA icons with no external dependencies (raw PNG via
// zlib). The mark echoes the in-app .brand-mark: two offset rounded squares
// (the "stacked photos" motif) in warm off-white on the near-black ink, full
// bleed so it doubles as a maskable icon.
//
//   node scripts/gen-icons.mjs
//
// Re-run if the brand colours change.

import { deflateSync } from "node:zlib";
import { writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const INK = [0x14, 0x13, 0x0f];
const BG = [0xf6, 0xf4, 0xef];

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "public", "icons");
mkdirSync(outDir, { recursive: true });

function crc32(buf) {
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, "ascii");
  const body = Buffer.concat([typeBuf, data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([len, body, crc]);
}

function encodePng(size, pixels) {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  // raw scanlines, filter byte 0 per row
  const stride = size * 4;
  const raw = Buffer.alloc((stride + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (stride + 1)] = 0;
    pixels.copy(raw, y * (stride + 1) + 1, y * stride, y * stride + stride);
  }
  const idat = deflateSync(raw, { level: 9 });
  return Buffer.concat([
    sig,
    chunk("IHDR", ihdr),
    chunk("IDAT", idat),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// Rounded-rect coverage helper for anti-aliased fills.
function roundedRectCoverage(px, py, x, y, w, h, r) {
  const cx = Math.min(Math.max(px, x + r), x + w - r);
  const cy = Math.min(Math.max(py, y + r), y + h - r);
  const dx = px - cx;
  const dy = py - cy;
  const inside = px >= x && px <= x + w && py >= y && py <= y + h;
  if (!inside) return 0;
  const dist = Math.sqrt(dx * dx + dy * dy);
  return Math.max(0, Math.min(1, r + 0.5 - dist));
}

function makeIcon(size) {
  const px = Buffer.alloc(size * size * 4);
  const s = size;
  // background full bleed (ink)
  for (let i = 0; i < s * s; i++) {
    px[i * 4] = INK[0];
    px[i * 4 + 1] = INK[1];
    px[i * 4 + 2] = INK[2];
    px[i * 4 + 3] = 255;
  }
  // two offset rounded squares, centred, within the maskable safe zone (~62%)
  const card = s * 0.34;
  const r = card * 0.18;
  const off = s * 0.075;
  const cx = s / 2;
  const cy = s / 2;
  const squares = [
    { x: cx - card / 2 - off, y: cy - card / 2 - off },
    { x: cx - card / 2 + off, y: cy - card / 2 + off },
  ];
  for (let y = 0; y < s; y++) {
    for (let x = 0; x < s; x++) {
      let cov = 0;
      for (const sq of squares) {
        cov = Math.max(cov, roundedRectCoverage(x + 0.5, y + 0.5, sq.x, sq.y, card, card, r));
      }
      if (cov > 0) {
        const i = (y * s + x) * 4;
        px[i] = Math.round(INK[0] + (BG[0] - INK[0]) * cov);
        px[i + 1] = Math.round(INK[1] + (BG[1] - INK[1]) * cov);
        px[i + 2] = Math.round(INK[2] + (BG[2] - INK[2]) * cov);
        px[i + 3] = 255;
      }
    }
  }
  return encodePng(s, px);
}

for (const size of [192, 512]) {
  const buf = makeIcon(size);
  writeFileSync(join(outDir, `icon-${size}.png`), buf);
  console.log(`wrote icon-${size}.png (${buf.length} bytes)`);
}
