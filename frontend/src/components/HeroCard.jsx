// The №1 result: large photo, final score, profile, and the one-line AI note
// rendered in quotes (the "why" behind the pick). Tapping the score breakdown
// toggle reveals the full transparent breakdown, same as the runner-up cards.

import { useMemo, useState } from "react";
import AxisBar from "./AxisBar.jsx";
import Photo from "./Photo.jsx";
import { buildBreakdown } from "../lib/breakdown.js";

export default function HeroCard({ photo, previewUrl, rankedBy, fallbackNote }) {
  const [open, setOpen] = useState(false);

  const { axes, total } = useMemo(
    () => buildBreakdown(photo.score_breakdown),
    [photo.score_breakdown],
  );

  // Gemini's note in quotes; otherwise the comparison note from lib/notes.js.
  const note = photo.notes
    ? { text: photo.notes, quoted: true }
    : { text: fallbackNote, quoted: false };

  const score = Number(photo.final_score ?? 0);

  return (
    <div className={"hero-card" + (open ? " open" : "")}>
      <div className="hero-photo">
        <Photo className="hero-img" src={previewUrl} alt="Top-ranked photo" />
        <div className="hero-rank-badge">
          <span className="rank-num">№1</span>
          <span>Best shot</span>
        </div>
        <div className="placeholder-tag">{photo.photo_id}</div>
      </div>
      <div className="hero-body">
        <div className="hero-score-row">
          <div className="display hero-score">
            {score.toFixed(1)}
            <span className="denom">/10</span>
          </div>
          <div className="hero-label-stack">
            <div className="eyebrow">Final score</div>
            <div className="label-main">{rankedBy}</div>
          </div>
        </div>
        <div className={"hero-note" + (note.quoted ? " quoted" : "")}>
          {note.text}
          <span className="by">PhotoRank · {rankedBy}</span>
        </div>

        <button
          className="hero-breakdown-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span className="eyebrow">Score breakdown</span>
          <span className="chev" aria-hidden="true">
            ⌄
          </span>
        </button>

        {open && (
          <div className="breakdown">
            <div className="breakdown-head">
              <div className="eyebrow">Score breakdown</div>
              <div className="breakdown-total mono">Σ {total.toFixed(2)}</div>
            </div>
            {axes.map((axis) => (
              <AxisBar key={axis.key} axis={axis} />
            ))}
            <div className="breakdown-foot">
              <div className="breakdown-key">
                <span className="dot" />
                Bar width = weight × raw score
              </div>
              <div className="breakdown-key">Dashed = axis max</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
