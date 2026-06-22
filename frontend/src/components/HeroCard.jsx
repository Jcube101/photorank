// The №1 result: large photo, final score, profile, and the one-line AI note
// rendered in quotes (the "why" behind the pick). Tapping the score breakdown
// toggle reveals the full transparent breakdown, same as the runner-up cards.

import { useMemo, useState } from "react";
import AxisBar from "./AxisBar.jsx";
import { buildBreakdown, fallbackNote } from "../lib/breakdown.js";

export default function HeroCard({ photo, previewUrl, profileLabel }) {
  const [open, setOpen] = useState(false);

  const { axes, total } = useMemo(
    () => buildBreakdown(photo.score_breakdown),
    [photo.score_breakdown],
  );

  const note = useMemo(() => {
    if (photo.notes) return { text: photo.notes, quoted: true };
    return { text: fallbackNote(axes), quoted: false };
  }, [photo.notes, axes]);

  const score = Number(photo.final_score ?? 0);

  return (
    <div className={"hero-card" + (open ? " open" : "")}>
      <div className="hero-photo">
        {previewUrl ? (
          <img className="hero-img" src={previewUrl} alt="Top-ranked photo" />
        ) : (
          <div className="placeholder-fill" />
        )}
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
            <div className="label-main">{profileLabel} profile</div>
          </div>
        </div>
        <div className={"hero-note" + (note.quoted ? " quoted" : "")}>
          {note.text}
          <span className="by">PhotoRank · {profileLabel} profile</span>
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
