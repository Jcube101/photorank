// A runner-up card: collapsed row (position, thumbnail, blurb, score) that
// expands to the full transparent score breakdown.

import { useMemo } from "react";
import AxisBar from "./AxisBar.jsx";
import { buildBreakdown, fallbackNote } from "../lib/breakdown.js";

export default function RankCard({ photo, rank, previewUrl, open, onToggle }) {
  const { axes, total } = useMemo(
    () => buildBreakdown(photo.score_breakdown),
    [photo.score_breakdown],
  );
  const blurb = photo.notes || fallbackNote(axes);
  const score = Number(photo.final_score ?? 0);

  return (
    <div className={"rank-card" + (open ? " open" : "")}>
      <button className="rank-row" onClick={onToggle} aria-expanded={open}>
        <div className="rank-pos">{String(rank).padStart(2, "0")}</div>
        <div className="rank-thumb">
          {previewUrl ? (
            <img src={previewUrl} alt="" />
          ) : (
            <div className="placeholder-fill" />
          )}
        </div>
        <div className="rank-text">
          <div className="rank-blurb">{blurb}</div>
          <div className="rank-sub">{photo.photo_id}</div>
        </div>
        <div className="rank-right">
          <div className="rank-score">
            {score.toFixed(1)}
            <span className="denom">/10</span>
          </div>
          <div className="chev" aria-hidden="true">
            ⌄
          </div>
        </div>
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
  );
}
