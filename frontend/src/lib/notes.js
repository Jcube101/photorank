// The note on a photo that has no Gemini note: every burst-mode photo, and any
// set-mode photo Gemini left blank.
//
// Each note compares the photo with the others in the same result, so it is
// true for that photo. Only №1 is called the best at anything "in this set";
// every other photo is described against №1 (where it falls short, and where
// it beats №1, if anywhere). Values are compared at the precision the score
// breakdown shows (one decimal), so a note never contradicts the numbers on
// screen. (The old note named each photo's own largest contribution, which
// is face sharpness at 50% weight, so every card said "Strongest on face
// sharpness in this set.")
//
// No imports, so it runs under Node (tests/test_notes.py).

/**
 * What each note claims, as data (tested directly against the scores).
 *
 * @param {Array<Array<{key: string, raw: number, weight: number}>>} photos
 *   The axes of each photo (as buildBreakdown returns them: weight > 0 only),
 *   in rank order, №1 first.
 * @returns {Array<object>} One claim per photo, in the same order:
 *   №1:     {kind: "lead", axis}   strictly highest on `axis` of all photos
 *           {kind: "joint", axis}  highest on `axis`, level with another photo
 *           {kind: "overall"}      highest on no single axis
 *           {kind: "alone"}        the only photo ranked
 *   others: {kind: "vs_top", worse, better}  the axis where it loses most to
 *           №1 (weight × gap), and the one where it gains most, or null
 */
export function noteClaims(photos) {
  const shown = photos.map(
    (axes) => new Map(axes.map((a) => [a.key, { ...a, raw: shownValue(a.raw) }])),
  );
  return shown.map((axes, i) => (i === 0 ? topClaim(shown) : versusTop(axes, shown[0])));
}

/**
 * One note per photo, in rank order.
 *
 * @param {Array<Array<{key: string, label: string, raw: number, weight: number}>>} photos
 * @param {{subset?: boolean}} [opts]  subset: some uploads were left out of
 *   the ranking (the blur gate), so "in this set" would overclaim.
 */
export function fallbackNotes(photos, { subset = false } = {}) {
  const labels = new Map(photos.flat().map((a) => [a.key, a.label]));
  const words = (key) => PHRASES[key] ?? generic(labels.get(key) ?? key);
  const scope = subset ? `of the ${photos.length} ranked` : "in this set";
  return noteClaims(photos).map((c) => {
    switch (c.kind) {
      case "alone":
        return "The only photo ranked.";
      case "lead":
        return `${words(c.axis).best} ${scope}.`;
      case "joint":
        return `Joint ${lower(words(c.axis).best)} ${scope}.`;
      case "overall":
        return `Best combined score ${scope}.`;
      default: {
        const worse = c.worse && words(c.worse).worse;
        const better = c.better && words(c.better).better;
        if (worse && better) return `${upper(better)} than №1, but ${worse}.`;
        if (worse) return `${upper(worse)} than №1.`;
        if (better) return `${upper(better)} than №1, level on the rest.`;
        return "Level with №1 on every measure.";
      }
    }
  });
}

// Burst-mode axes read better in plain words than as "higher face sharpness".
const PHRASES = {
  face_sharpness: { best: "Sharpest face", better: "sharper face", worse: "softer face" },
  sharpness: { best: "Sharpest overall", better: "sharper overall", worse: "softer overall" },
  face_exposure: {
    best: "Best-exposed face", better: "better-exposed face", worse: "face less well exposed",
  },
  exposure: { best: "Best exposure", better: "better exposure", worse: "weaker exposure" },
};

function generic(label) {
  const l = lower(label);
  return { best: `Highest ${l}`, better: `higher ${l}`, worse: `lower ${l}` };
}

function topClaim(shown) {
  const [top, ...others] = shown;
  if (others.length === 0) return { kind: "alone" };
  let lead = null;
  let joint = null;
  for (const a of top.values()) {
    const rivals = others.map((m) => m.get(a.key)?.raw).filter((v) => v !== undefined);
    const next = Math.max(...rivals);
    if (a.raw > next) {
      const margin = (a.raw - next) * a.weight;
      if (!lead || margin > lead.margin) lead = { axis: a.key, margin };
    } else if (a.raw === next && (!joint || a.weight > joint.weight)) {
      joint = { axis: a.key, weight: a.weight };
    }
  }
  if (lead) return { kind: "lead", axis: lead.axis };
  if (joint) return { kind: "joint", axis: joint.axis };
  return { kind: "overall" };
}

function versusTop(axes, top) {
  let worse = null;
  let better = null;
  for (const a of axes.values()) {
    const t = top.get(a.key);
    if (!t || a.raw === t.raw) continue;
    const gap = (a.raw - t.raw) * a.weight;
    if (gap < 0 && (!worse || gap < worse.gap)) worse = { axis: a.key, gap };
    if (gap > 0 && (!better || gap > better.gap)) better = { axis: a.key, gap };
  }
  return { kind: "vs_top", worse: worse?.axis ?? null, better: better?.axis ?? null };
}

// The value as AxisBar shows it (toFixed(1)).
function shownValue(raw) {
  return Number(Number(raw).toFixed(1));
}

function lower(s) {
  return s.charAt(0).toLowerCase() + s.slice(1);
}
function upper(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
