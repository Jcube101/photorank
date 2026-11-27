// Photos the user picked that are not in the ranking, and why. The results
// screen names every one, so a batch never shows fewer photos than were picked
// (a 5-photo burst once showed 2 cards and said nothing about the other 3:
// the server's blur gate had dropped them; LEARNINGS 2026-11-27).
//
// Sources, in order: photos the phone couldn't prepare (never sent; see
// compress.js), the server's `left_out` (SPECS §6.1), and, as a safety net,
// any uploaded photo the server returned in neither list.
//
// No imports beyond the wording helpers, so it runs under Node (tests/test_left_out.py).

import { listNames } from "./unprepared.js";

/**
 * @param {object} result  The /rank response, plus the app's own `unprepared`
 *   and `uploaded` name lists (App.jsx).
 * @returns {Array<{photo_id: string, reason: string, blur_raw?: number}>}
 *   reason: "unprepared" | "blurry" | "unreadable" | "unscorable" | "missing"
 */
export function leftOutPhotos(result) {
  const ranked = new Set((result.ranked ?? []).map((p) => p.photo_id));
  const seen = new Set();
  const out = [];
  const add = (entry) => {
    if (ranked.has(entry.photo_id) || seen.has(entry.photo_id)) return;
    seen.add(entry.photo_id);
    out.push(entry);
  };
  for (const name of result.unprepared ?? []) add({ photo_id: name, reason: "unprepared" });
  for (const entry of result.left_out ?? []) add(entry);
  for (const name of result.uploaded ?? []) add({ photo_id: name, reason: "missing" });
  return out;
}

/** Why one photo was left out, for its card. */
export function leftOutReason(entry, threshold) {
  switch (entry.reason) {
    case "blurry":
      return `Too blurry to rank: blur check ${Math.round(entry.blur_raw)}, needs ${threshold}.`;
    case "unprepared":
      return "Couldn't be prepared for upload, so it wasn't sent.";
    case "unreadable":
      return "The server couldn't read this photo.";
    case "unscorable":
      return "This photo couldn't be scored.";
    default:
      return "The server didn't return this photo.";
  }
}

// One line per reason, naming the photos, for the notice at the top.
const NOTICE = [
  ["unprepared", (names, n) => `Couldn't prepare ${names} for upload, so ${n === 1 ? "it wasn't" : "they weren't"} ranked.`],
  ["blurry", (names) => `Too blurry to rank: ${names}.`],
  ["unreadable", (names) => `The server couldn't read ${names}.`],
  ["unscorable", (names) => `Couldn't score ${names}.`],
  ["missing", (names) => `Not returned by the server: ${names}.`],
];

/** The notice lines naming every left-out photo, grouped by reason. */
export function leftOutNotice(entries) {
  const lines = [];
  for (const [reason, line] of NOTICE) {
    const names = entries.filter((e) => (e.reason in REASONS ? e.reason : "missing") === reason)
      .map((e) => e.photo_id);
    if (names.length) lines.push(line(listNames(names), names.length));
  }
  return lines;
}

const REASONS = { unprepared: 1, blurry: 1, unreadable: 1, unscorable: 1, missing: 1 };
