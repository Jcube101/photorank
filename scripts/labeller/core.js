/*
 * Pure logic for the shoot labeller (scripts/label_shoot.py). No DOM.
 *
 * The page loads this as a plain script (window.LabelCore). The tests load
 * the same file through Node's require(), so what they check is what the
 * page runs.
 *
 * State is held in photo ids, not indices, so saved progress survives a
 * rebuild of the page:
 *   starts  ids of photos that begin a set. Never the first photo.
 *   picks   ids of picked photos. The UI keeps at most one per set.
 *
 * The exported key follows eval/README.md (docs/V2-PLAN.md §12.1).
 * validateKey() mirrors scripts/eval_shoot.py:resolve_key(), which the
 * evaluation runs on the same file.
 */
(function (root) {
  "use strict";

  /** Filename without its extension, as Python's Path(name).stem. */
  function stem(id) {
    const dot = id.lastIndexOf(".");
    return dot > 0 ? id.slice(0, dot) : id;
  }

  function indexMap(photoIds) {
    const index = new Map();
    photoIds.forEach((id, i) => index.set(id, i));
    return index;
  }

  function byPosition(photoIds, ids) {
    const index = indexMap(photoIds);
    return ids.slice().sort((a, b) => index.get(a) - index.get(b));
  }

  /** Sets as [{start, end}] photo indices, end exclusive, in shooting order. */
  function setsOf(photoIds, starts) {
    const index = indexMap(photoIds);
    const cuts = [...new Set(starts.map((id) => index.get(id)))]
      .filter((i) => i !== undefined && i > 0)
      .sort((a, b) => a - b);
    const sets = [];
    let start = 0;
    for (const cut of cuts) {
      sets.push({ start, end: cut });
      start = cut;
    }
    if (photoIds.length) sets.push({ start, end: photoIds.length });
    return sets;
  }

  /** Position in `sets` of the set holding photo index i. */
  function setIndexOf(sets, i) {
    return sets.findIndex((s) => s.start <= i && i < s.end);
  }

  /** Drop unknown and duplicate ids, and a start on the first photo. */
  function normalize(state, photoIds) {
    const known = new Set(photoIds);
    const clean = (ids) =>
      byPosition(photoIds, [...new Set(Array.isArray(ids) ? ids : [])].filter((id) => known.has(id)));
    return {
      starts: clean(state && state.starts).filter((id) => id !== photoIds[0]),
      picks: clean(state && state.picks),
    };
  }

  function picksIn(photoIds, picks, set) {
    const index = indexMap(photoIds);
    return picks.filter((id) => index.get(id) >= set.start && index.get(id) < set.end);
  }

  /** Start a new set at photoId. A no-op on the first photo or an existing start. */
  function split(state, photoIds, photoId) {
    const i = photoIds.indexOf(photoId);
    if (i <= 0 || state.starts.includes(photoId)) return state;
    return { starts: byPosition(photoIds, [...state.starts, photoId]), picks: state.picks.slice() };
  }

  /**
   * Join the set that starts at startId to the one before it. If both had a
   * pick, the merged set gets none: each was chosen against a different set,
   * so the labeller chooses again.
   */
  function mergeWithPrevious(state, photoIds, startId) {
    if (!state.starts.includes(startId)) return state;
    const starts = state.starts.filter((id) => id !== startId);
    const sets = setsOf(photoIds, starts);
    const merged = sets[setIndexOf(sets, photoIds.indexOf(startId))];
    const inside = picksIn(photoIds, state.picks, merged);
    const picks = inside.length > 1 ? state.picks.filter((id) => !inside.includes(id)) : state.picks.slice();
    return { starts, picks };
  }

  /** Make photoId the pick of its set, replacing any other pick in that set. */
  function choosePick(state, photoIds, photoId) {
    const i = photoIds.indexOf(photoId);
    if (i < 0) return state;
    const sets = setsOf(photoIds, state.starts);
    const inside = picksIn(photoIds, state.picks, sets[setIndexOf(sets, i)]);
    const picks = state.picks.filter((id) => !inside.includes(id));
    return { starts: state.starts.slice(), picks: byPosition(photoIds, [...picks, photoId]) };
  }

  /**
   * Edits against the engine's proposal, counted as in docs/V2-PLAN.md §12.2:
   * a removed proposed boundary is a merge, an added one is a split.
   */
  function corrections(proposalStarts, starts) {
    const proposed = new Set(proposalStarts);
    const current = new Set(starts);
    const merges = proposalStarts.filter((id) => !current.has(id)).length;
    const splits = starts.filter((id) => !proposed.has(id)).length;
    return { merges, splits, total: merges + splits };
  }

  function rangeLabel(photoIds, set) {
    const first = stem(photoIds[set.start]);
    const last = stem(photoIds[set.end - 1]);
    return first === last ? first : first + "–" + last;
  }

  /**
   * Check an answer key against the photos, as resolve_key() does: sets in
   * shooting order cover every photo with no gaps or overlaps, and each pick
   * (and any "acceptable" frame) sits inside its set.
   *
   * Returns a list of {set, message}; empty means valid. `set` is the 1-based
   * position of the offending set, or null for a problem with the whole key.
   */
  function validateKey(key, photoIds) {
    const index = new Map();
    for (const [i, id] of photoIds.entries()) {
      const name = stem(id);
      if (index.has(name)) {
        return [{ set: null, message: `Two photos share the name ${name}; the key cannot tell them apart` }];
      }
      index.set(name, i);
    }
    if (!key || !Array.isArray(key.sets) || key.sets.length === 0) {
      return [{ set: null, message: "The key has no sets" }];
    }

    const problems = [];
    let expected = 0;
    key.sets.forEach((entry, n) => {
      const report = (message) => problems.push({ set: n + 1, message: `Set ${entry.set ?? n + 1}: ${message}` });
      const missing = ["first", "last", "pick"].filter((f) => entry[f] === undefined || entry[f] === null);
      if (missing.length) return report(`missing ${missing.join(", ")}`);
      const acceptable = Array.isArray(entry.acceptable) ? entry.acceptable : [];
      const unknown = [entry.first, entry.last, entry.pick, ...acceptable].filter((name) => !index.has(name));
      if (unknown.length) return report(`not in the input folder: ${unknown.join(", ")}`);

      const first = index.get(entry.first);
      const last = index.get(entry.last);
      if (last < first) return report("'last' comes before 'first' in shooting order");
      if (first < expected) report("overlaps the previous set");
      else if (first > expected) {
        report(`leaves ${first - expected} photo(s) uncovered before it (from ${stem(photoIds[expected])})`);
      }
      expected = last + 1;

      const pick = index.get(entry.pick);
      if (pick < first || pick > last) report(`pick ${entry.pick} is outside the set`);
      const outside = acceptable.filter((name) => index.get(name) < first || index.get(name) > last);
      if (outside.length) report(`acceptable ${outside.join(", ")} outside the set`);
    });

    if (!problems.length && expected !== photoIds.length) {
      problems.push({
        set: null,
        message: `${photoIds.length - expected} photo(s) after the last set are uncovered (from ${stem(photoIds[expected])})`,
      });
    }
    if (key.photo_count !== undefined && key.photo_count !== null && key.photo_count !== photoIds.length) {
      problems.push({ set: null, message: `photo_count is ${key.photo_count} but the shoot has ${photoIds.length} photos` });
    }
    return problems;
  }

  /**
   * The answer key for the current state, and every reason it cannot be
   * exported yet. Each set needs exactly one pick. The key is then checked
   * again with validateKey(), independently of how it was built.
   */
  function buildKey(meta, photoIds, state) {
    const problems = [];
    const sets = setsOf(photoIds, state.starts).map((set, n) => {
      const picks = picksIn(photoIds, state.picks, set);
      const label = `Set ${n + 1} (${rangeLabel(photoIds, set)})`;
      if (picks.length === 0) problems.push({ set: n + 1, message: `${label} has no pick` });
      if (picks.length > 1) problems.push({ set: n + 1, message: `${label} has ${picks.length} picks; keep one` });
      return {
        set: n + 1,
        first: stem(photoIds[set.start]),
        last: stem(photoIds[set.end - 1]),
        pick: picks.length === 1 ? stem(picks[0]) : null,
      };
    });

    const key = { shoot: meta.shoot };
    if (meta.camera) key.camera = meta.camera;
    key.photo_count = photoIds.length;
    key.sets = sets;
    return { key, problems: problems.length ? problems : validateKey(key, photoIds) };
  }

  /** JSON text for a key, one line per set, like the example in eval/README.md. */
  function formatKey(key) {
    const pair = ([k, v]) => JSON.stringify(k) + ": " + JSON.stringify(v);
    const head = Object.entries(key).filter(([k]) => k !== "sets").map((kv) => "  " + pair(kv));
    const sets = key.sets.map((s) => "    { " + Object.entries(s).map(pair).join(", ") + " }");
    return "{\n" + head.join(",\n") + ',\n  "sets": [\n' + sets.join(",\n") + "\n  ]\n}\n";
  }

  const api = {
    stem, setsOf, setIndexOf, normalize, split, mergeWithPrevious, choosePick,
    corrections, rangeLabel, validateKey, buildKey, formatKey,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LabelCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
