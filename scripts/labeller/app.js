/*
 * Shoot labeller UI. Logic lives in core.js (window.LabelCore); this file
 * only draws state and turns taps into core calls.
 *
 * Shows no AI scores or rankings: the page data has none.
 */
(function () {
  "use strict";

  const C = window.LabelCore;
  const DATA = JSON.parse(document.getElementById("labeller-data").textContent);
  const IDS = DATA.photos.map((p) => p.id);
  const META = { shoot: DATA.shoot, camera: DATA.camera };
  const PROPOSAL_STARTS = DATA.proposal.slice(1).map((b) => b.start);
  const PROPOSED = new Map(DATA.proposal.map((b) => [b.start, b]));
  const STORE_KEY = "photorank-labeller:" + DATA.shoot + ":" + DATA.photo_hash;
  const HISTORY_MAX = 100;

  const $ = (sel) => document.querySelector(sel);

  let state = proposal();
  let history = [];
  let showProblems = false;
  let viewing = null;          // photo index open in the viewer, or null

  function proposal() {
    return { starts: PROPOSAL_STARTS.slice(), picks: [] };
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(className, text, label) {
    const b = el("button", className, text);
    b.type = "button";
    if (label) {
      b.setAttribute("aria-label", label);
      b.title = label;
    }
    return b;
  }

  function fmtSeconds(s) {
    if (s < 60) return Math.round(s) + " s";
    const m = Math.floor(s / 60);
    const rest = Math.round(s - m * 60);
    return rest ? `${m} min ${rest} s` : `${m} min`;
  }

  function fmtGap(s) {
    return s < 60 ? `+${Math.round(s)}s` : `+${(s / 60).toFixed(1)}m`;
  }

  // ------------------------------------------------------------------ storage

  function load() {
    let raw = null;
    try {
      raw = localStorage.getItem(STORE_KEY);
    } catch (e) {
      return;
    }
    if (!raw) return;
    try {
      const saved = JSON.parse(raw);
      state = C.normalize(saved, IDS);
      if (saved.proposal_hash !== DATA.proposal_hash) {
        notice("The proposed sets changed since you started. Your picks and edits are kept; Reset starts again from the new proposal.");
      }
    } catch (e) {
      notice("Saved progress could not be read, so this starts from the proposal.");
    }
  }

  function save() {
    let ok = true;
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        starts: state.starts,
        picks: state.picks,
        proposal_hash: DATA.proposal_hash,
        saved_at: Date.now(),
      }));
    } catch (e) {
      ok = false;
    }
    const s = $("#saved");
    s.textContent = ok ? "saved in this browser" : "not saved: browser storage is unavailable";
    s.classList.toggle("warn-text", !ok);
  }

  function apply(next) {
    if (next === state || JSON.stringify(next) === JSON.stringify(state)) return;
    history.push(state);
    if (history.length > HISTORY_MAX) history.shift();
    state = next;
    hideExported();
    save();
    render();
  }

  function undo() {
    if (!history.length) return;
    state = history.pop();
    hideExported();
    save();
    render();
  }

  function reset() {
    if (!confirm("Discard your picks and edits and go back to the proposed sets? Undo can still bring them back.")) return;
    apply(proposal());
  }

  // -------------------------------------------------------------------- tiles

  const tiles = new Map();

  function makeTile(p) {
    const fig = el("figure", "tile");
    const shot = button("shot", undefined, `Make ${p.stem} the pick`);
    const img = el("img");
    img.src = p.thumb;
    img.alt = p.stem;
    img.loading = "lazy";
    img.decoding = "async";
    img.width = p.w;
    img.height = p.h;
    shot.append(img, el("span", "badge", "Pick"));

    const cap = el("figcaption");
    cap.append(el("span", "name", p.stem));
    if (p.gap_s !== null) cap.append(el("span", "gap", fmtGap(p.gap_s)));
    cap.append(el("span", "merged", "merged"));

    const actions = el("div", "actions");
    const split = button("split", "✂ Split", `Split before ${p.stem}: start a new set here`);
    const view = button("view", "⤢", `View ${p.stem} larger`);
    actions.append(split, view);
    fig.append(shot, cap, actions);

    shot.addEventListener("click", () => apply(C.choosePick(state, IDS, p.id)));
    split.addEventListener("click", () => apply(C.split(state, IDS, p.id)));
    view.addEventListener("click", () => openViewer(IDS.indexOf(p.id)));
    return fig;
  }

  function reasonChip(startId, n) {
    if (n === 0) return el("span", "chip", "start of shoot");
    const b = PROPOSED.get(startId);
    if (!b) return el("span", "chip you", "your split");
    const chip = el("span", "chip reason");
    chip.append(el("b", undefined, b.reason));
    if (b.dt_s !== null) chip.append(document.createTextNode(" · " + fmtSeconds(b.dt_s) + " gap"));
    const detail = [];
    if (b.dh !== null) detail.push("dh " + b.dh);
    if (b.dc !== null) detail.push("dc " + b.dc.toFixed(2));
    if (detail.length) chip.append(el("span", "detail", " · " + detail.join(" · ")));
    chip.title = "Proposed boundary. dt: seconds since the previous photo; dh: dHash distance (0–64); dc: colour histogram distance (0–1).";
    return chip;
  }

  // ------------------------------------------------------------------- render

  function render() {
    const sets = C.setsOf(IDS, state.starts);
    const picks = new Set(state.picks);
    const proposed = new Set(PROPOSAL_STARTS);
    const result = C.buildKey(META, IDS, state);
    const flagged = new Set(showProblems ? result.problems.map((p) => p.set) : []);

    const frag = document.createDocumentFragment();
    let picked = 0;
    sets.forEach((set, n) => {
      const ids = IDS.slice(set.start, set.end);
      const setPicks = ids.filter((id) => picks.has(id));
      if (setPicks.length === 1) picked++;

      const sec = el("section", "set");
      sec.id = "set-" + (n + 1);
      sec.classList.toggle("needs-pick", setPicks.length !== 1);
      sec.classList.toggle("problem", flagged.has(n + 1));

      const head = el("header", "set-head");
      const title = el("div", "set-title");
      title.append(
        el("strong", undefined, "Set " + (n + 1)),
        el("span", "muted", ` · ${ids.length} photo${ids.length === 1 ? "" : "s"} · ${C.rangeLabel(IDS, set)}`),
      );
      head.append(title, reasonChip(ids[0], n));
      if (setPicks.length === 0) head.append(el("span", "chip warn", "needs a pick"));
      if (setPicks.length > 1) head.append(el("span", "chip warn", setPicks.length + " picks: tap one"));
      if (n > 0) {
        const merge = button("merge", "Merge with previous", `Merge set ${n + 1} into set ${n}`);
        merge.addEventListener("click", () => apply(C.mergeWithPrevious(state, IDS, ids[0])));
        head.append(merge);
      }

      const grid = el("div", "grid");
      ids.forEach((id, k) => {
        const tile = tiles.get(id);
        tile.classList.toggle("picked", picks.has(id));
        tile.classList.toggle("first", k === 0);
        tile.classList.toggle("was-start", k > 0 && proposed.has(id));
        grid.append(tile);
      });
      sec.append(head, grid);
      frag.append(sec);
    });
    $("#sets").replaceChildren(frag);

    const fix = C.corrections(PROPOSAL_STARTS, state.starts);
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    $("#stats").textContent = `${picked}/${sets.length} picked · ${plural(sets.length, "set")} · ${IDS.length} photos`;
    $("#corrections").textContent = fix.total
      ? `${plural(fix.total, "correction")}: ${plural(fix.merges, "merge")}, ${plural(fix.splits, "split")}`
      : "no corrections yet";
    $("#corrections").title = "Edits to the engine's proposed sets. A removed boundary is a merge, an added one a split.";
    $("#undo").disabled = history.length === 0;
    $("#next").disabled = picked === sets.length;
    renderProblems(result.problems);
    if (viewing !== null) showViewer();
  }

  function renderProblems(problems) {
    const box = $("#problems");
    if (!showProblems) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    box.replaceChildren();
    if (!problems.length) {
      box.className = "panel ok";
      box.append(el("p", undefined, "Every set has one pick. Export again to download the key."));
      return;
    }
    box.className = "panel bad";
    box.append(el("p", undefined, `Can't export yet: ${problems.length} problem${problems.length === 1 ? "" : "s"}.`));
    const list = el("ul");
    for (const p of problems.slice(0, 50)) {
      const item = el("li");
      if (p.set) {
        const link = button("link", p.message);
        link.addEventListener("click", () => scrollToSet(p.set));
        item.append(link);
      } else {
        item.textContent = p.message;
      }
      list.append(item);
    }
    box.append(list);
  }

  function scrollToSet(n) {
    const sec = document.getElementById("set-" + n);
    if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function nextUnpicked() {
    const open = [...document.querySelectorAll(".set.needs-pick")];
    if (!open.length) return;
    const line = $(".bar").getBoundingClientRect().bottom + 8;
    const target = open.find((s) => s.getBoundingClientRect().top > line) || open[0];
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function notice(text) {
    const box = $("#notice");
    box.hidden = false;
    box.textContent = text;
  }

  // ------------------------------------------------------------------- export

  function exportKey() {
    const result = C.buildKey(META, IDS, state);
    if (result.problems.length) {
      showProblems = true;
      hideExported();
      render();
      const first = result.problems.find((p) => p.set);
      if (first) scrollToSet(first.set);
      else $("#problems").scrollIntoView({ behavior: "smooth" });
      return;
    }
    showProblems = false;
    render();
    const text = C.formatKey(result.key);
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = el("a");
    a.href = url;
    a.download = "answer_key.json";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);

    const box = $("#exported");
    box.hidden = false;
    $("#exported-summary").textContent =
      `answer_key.json downloaded: ${result.key.sets.length} sets, ${IDS.length} photos.`;
    $("#exported-json").value = text;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function hideExported() {
    $("#exported").hidden = true;
  }

  async function copyKey() {
    const area = $("#exported-json");
    const status = $("#copy-status");
    try {
      await navigator.clipboard.writeText(area.value);
      status.textContent = "Copied.";
    } catch (e) {
      area.select();
      status.textContent = "Selected: press Ctrl+C (or ⌘C) to copy.";
    }
  }

  // ------------------------------------------------------------------- viewer

  const lb = $("#viewer");
  const lbImg = $("#viewer-img");

  function openViewer(i) {
    viewing = i;
    lb.hidden = false;
    document.body.classList.add("noscroll");
    showViewer();
    $("#viewer-close").focus();
  }

  function closeViewer() {
    if (viewing === null) return;
    const id = IDS[viewing];
    viewing = null;
    lb.hidden = true;
    lbImg.removeAttribute("src");
    document.body.classList.remove("noscroll");
    const tile = tiles.get(id);
    if (tile) tile.querySelector(".view").focus({ preventScroll: true });
  }

  function step(delta) {
    if (viewing === null) return;
    const next = viewing + delta;
    if (next < 0 || next >= IDS.length) return;
    viewing = next;
    showViewer();
  }

  function showViewer() {
    const p = DATA.photos[viewing];
    lbImg.src = p.large || p.thumb;
    lbImg.alt = p.stem;
    const sets = C.setsOf(IDS, state.starts);
    const n = C.setIndexOf(sets, viewing);
    const set = sets[n];
    const parts = [p.stem, `Set ${n + 1}`, `${viewing - set.start + 1} of ${set.end - set.start}`];
    if (p.time) parts.push(p.time);
    if (p.gap_s !== null) parts.push(fmtGap(p.gap_s) + " since previous");
    $("#viewer-caption").textContent = parts.join(" · ");

    const isPick = state.picks.includes(p.id);
    const pick = $("#viewer-pick");
    pick.textContent = isPick ? "✓ Pick" : "Make pick";
    pick.classList.toggle("on", isPick);
    $("#viewer-split").disabled = viewing === set.start;
    $("#viewer-prev").disabled = viewing === 0;
    $("#viewer-next").disabled = viewing === IDS.length - 1;

    for (const j of [viewing - 1, viewing + 1]) {       // warm the neighbours
      const q = DATA.photos[j];
      if (q && q.large) new Image().src = q.large;
    }
  }

  let touchX = null;
  let touchY = null;
  $("#viewer-stage").addEventListener("touchstart", (e) => {
    touchX = e.changedTouches[0].clientX;
    touchY = e.changedTouches[0].clientY;
  }, { passive: true });
  $("#viewer-stage").addEventListener("touchend", (e) => {
    if (touchX === null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    const dy = e.changedTouches[0].clientY - touchY;
    touchX = touchY = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1);
  });

  // ------------------------------------------------------------------- wiring

  $("#viewer-close").addEventListener("click", closeViewer);
  $("#viewer-prev").addEventListener("click", () => step(-1));
  $("#viewer-next").addEventListener("click", () => step(1));
  $("#viewer-pick").addEventListener("click", () => apply(C.choosePick(state, IDS, IDS[viewing])));
  $("#viewer-split").addEventListener("click", () => apply(C.split(state, IDS, IDS[viewing])));
  $("#undo").addEventListener("click", undo);
  $("#next").addEventListener("click", nextUnpicked);
  $("#export").addEventListener("click", exportKey);
  $("#reset").addEventListener("click", reset);
  $("#copy").addEventListener("click", copyKey);
  $("#exported-close").addEventListener("click", hideExported);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
      if (e.target instanceof HTMLTextAreaElement) return;
      e.preventDefault();
      undo();
      return;
    }
    if (viewing === null) return;
    if (e.key === "Escape") closeViewer();
    else if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "ArrowRight") step(1);
    else if (e.key.toLowerCase() === "p") apply(C.choosePick(state, IDS, IDS[viewing]));
    else if (e.key.toLowerCase() === "s") apply(C.split(state, IDS, IDS[viewing]));
  });

  // Keep the sticky bar from covering a set scrolled into view.
  const bar = $(".bar");
  const setBarHeight = () =>
    document.documentElement.style.setProperty("--bar-h", bar.offsetHeight + "px");
  if ("ResizeObserver" in window) new ResizeObserver(setBarHeight).observe(bar);
  setBarHeight();

  // Remember whether the help panel was collapsed (a convenience only).
  const help = $("#help");
  const HELP_KEY = "photorank-labeller:help";
  try {
    if (localStorage.getItem(HELP_KEY) === "closed") help.open = false;
  } catch (e) { /* storage unavailable: keep it open */ }
  help.addEventListener("toggle", () => {
    try {
      localStorage.setItem(HELP_KEY, help.open ? "open" : "closed");
    } catch (e) { /* ignore */ }
  });

  $("#shoot").textContent = DATA.shoot;
  if (DATA.order !== "capture_time") {
    notice("Capture times were missing on at least one photo, so photos are in filename order.");
  }
  for (const p of DATA.photos) tiles.set(p.id, makeTile(p));
  load();
  save();
  render();
})();
