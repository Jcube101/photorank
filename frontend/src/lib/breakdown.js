// Normalize an API score_breakdown into rows the AxisBar can render.
//
// Works for both modes: set mode returns 6 axes (some with weight 0, e.g.
// camera_engagement outside the family profile); burst mode returns four
// face/full-image axes. We render whatever the API sent — never hardcoded.
//
// Bar geometry follows the approved design's rule:
//   bar width    = contribution, scaled so the heaviest axis at a perfect 10
//                  would fill the track  (maxContrib = max(weight) * 10)
//   dashed cap   = this axis's own ceiling (weight * 10), on the same scale
// So the filled bar is literally "weight × raw score" and the dashed line is
// "this axis's max" — exactly the legend shown to the user.

import { axisLabel } from "../config.js";

export function buildBreakdown(scoreBreakdown) {
  if (!scoreBreakdown || typeof scoreBreakdown !== "object") {
    return { axes: [], total: 0 };
  }

  const axes = Object.entries(scoreBreakdown)
    .map(([key, v]) => {
      // Prefer effective_weight (what actually applied) so raw × weight ===
      // contribution holds exactly in the math row.
      const weight = num(v.effective_weight, num(v.weight, 0));
      const raw = num(v.raw, 0);
      const contribution = num(v.contribution, raw * weight);
      const source = v.source || "";
      return {
        key,
        label: axisLabel(key),
        raw,
        weight,
        contribution,
        source,
        isGemini: /gemini/i.test(source),
      };
    })
    // Hide axes that carry no weight in this profile (e.g. camera_engagement
    // outside family). A 0% row contributes nothing and only confuses — show
    // only axes that actually move the score.
    .filter((a) => a.weight > 0);

  // Heaviest axis at a perfect score sets the full-bar reference.
  const maxContrib = Math.max(0.0001, ...axes.map((a) => a.weight * 10));
  for (const a of axes) {
    a.widthPct = clampPct((a.contribution / maxContrib) * 100);
    a.capPct = clampPct(((a.weight * 10) / maxContrib) * 100);
  }

  const total = axes.reduce((s, a) => s + a.contribution, 0);
  return { axes, total };
}

// Burst mode has no Gemini `notes`. Synthesize a short, honest blurb from the
// axis that contributed most, so the card/hero never shows an empty quote.
export function fallbackNote(axes) {
  if (!axes || axes.length === 0) return "Ranked on technical quality.";
  const top = [...axes].sort((a, b) => b.contribution - a.contribution)[0];
  return `Strongest on ${top.label.toLowerCase()} in this set.`;
}

function num(v, fallback) {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}
function clampPct(p) {
  return Math.max(0, Math.min(100, p));
}
