// One row of a score breakdown: axis name + weight, the explicit
// "raw × weight = contribution" math, and the contribution bar with a dashed
// cap marking the axis's maximum. Gemini-scored axes use the accent colour so
// the user can see which signals are semantic vs. deterministic.

export default function AxisBar({ axis }) {
  const { label, raw, weight, contribution, isGemini, isZero, widthPct, capPct, source } = axis;

  return (
    <div className={"axis" + (isZero ? " muted-axis" : "")}>
      <div className="axis-name">
        {label}
        <span className="weight">{Math.round(weight * 100)}%</span>
        {source && <span className="src">{isGemini ? "AI" : "CV"}</span>}
      </div>
      <div className="axis-vals">
        <span className="raw">{raw.toFixed(1)}</span>
        <span className="sep">×</span>
        <span>{weight.toFixed(2)}</span>
        <span className="sep">=</span>
        <span className="contrib">{contribution.toFixed(2)}</span>
      </div>
      <div className="axis-bar-wrap">
        <div className="axis-bar-cap" style={{ width: `${capPct}%` }} />
        <div
          className={"axis-bar" + (isGemini ? " gemini" : "")}
          style={{ width: `${widthPct}%` }}
        />
      </div>
    </div>
  );
}
