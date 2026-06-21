// Client-side profile re-ranking.
//
// Profiles are just weights: a set-mode run scores every photo on all six axes
// regardless of profile (camera_engagement is included at weight 0 in profiles
// that don't use it), so switching profiles is purely re-applying weights to
// data already in the response — no re-upload, no Gemini call, instant, offline.
//
// This mirrors core/rank.py:rank_photos EXACTLY so the on-device ordering
// matches a real server run: same weighted sum, same round-to-3, same sort key
// (final_score desc, then relative_rank as the tiebreaker). For family /
// portrait / event the result is identical to a true run; for travel it is
// directional only (see PROFILE_HINTED in config.js).

import { ALL_AXES, AXES_DETERMINISTIC } from "../config.js";

const round3 = (n) => Math.round(n * 1000) / 1000;

/**
 * Re-rank a set-mode `ranked` array under a different profile's weights.
 * Returns a NEW array of new photo objects (the original result is untouched,
 * so switching back and forth always re-derives from the source scores).
 *
 * @param {Array<object>} ranked  - result.ranked from the API (set mode)
 * @param {Record<string, number>} weights - profile weights summing to 1.0
 */
export function rerankByProfile(ranked, weights) {
  const rescored = ranked.map((photo) => {
    // The server weights the top-level raw axis values (s[axis]); the same
    // values are mirrored in score_breakdown[axis].raw. Fall back to the
    // breakdown so this is robust if a top-level field is ever absent.
    const rawOf = (axis) =>
      num(photo[axis], num(photo.score_breakdown?.[axis]?.raw, 0));

    const final_score = round3(
      ALL_AXES.reduce((sum, axis) => sum + rawOf(axis) * (weights[axis] ?? 0), 0),
    );

    const score_breakdown = {};
    for (const axis of ALL_AXES) {
      const w = weights[axis] ?? 0;
      const raw = rawOf(axis);
      score_breakdown[axis] = {
        raw,
        weight: w,
        effective_weight: w,
        contribution: round3(raw * w),
        source: AXES_DETERMINISTIC.includes(axis) ? "deterministic" : "gemini",
      };
    }

    return { ...photo, final_score, score_breakdown };
  });

  rescored.sort((a, b) => {
    if (b.final_score !== a.final_score) return b.final_score - a.final_score;
    return (a.relative_rank ?? 999) - (b.relative_rank ?? 999);
  });
  rescored.forEach((p, i) => {
    p.final_rank = i + 1;
  });

  return rescored;
}

function num(v, fallback) {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}
