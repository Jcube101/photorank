// The №1 result: large photo, final score, profile, and the one-line AI note
// rendered in quotes (the "why" behind the pick).

import { useMemo } from "react";
import { buildBreakdown, fallbackNote } from "../lib/breakdown.js";

export default function HeroCard({ photo, previewUrl, profileLabel }) {
  const note = useMemo(() => {
    if (photo.notes) return { text: photo.notes, quoted: true };
    const { axes } = buildBreakdown(photo.score_breakdown);
    return { text: fallbackNote(axes), quoted: false };
  }, [photo]);

  const score = Number(photo.final_score ?? 0);

  return (
    <div className="hero-card">
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
      </div>
    </div>
  );
}
