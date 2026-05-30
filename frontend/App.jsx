// PhotoRank — three screens: upload, loading, results
const { useState, useEffect, useRef } = React;

// ---------- Data ----------
const PROFILES = [
  { id: "family", name: "Family", desc: "Everyone smiling, eyes open" },
  { id: "portrait", name: "Portrait", desc: "Sharp face, soft background" },
  { id: "travel", name: "Travel", desc: "Composition & exposure first" },
  { id: "event", name: "Event", desc: "Moment, energy, light" },
];

// Weights per profile (sum to 1.0). Used both for breakdown and contribution math.
const WEIGHTS = {
  family:   { sharpness: 0.15, expression: 0.40, composition: 0.15, exposure: 0.15, focus: 0.15 },
  portrait: { sharpness: 0.25, expression: 0.25, composition: 0.20, exposure: 0.10, focus: 0.20 },
  travel:   { sharpness: 0.18, expression: 0.07, composition: 0.30, exposure: 0.25, focus: 0.20 },
  event:    { sharpness: 0.15, expression: 0.30, composition: 0.15, exposure: 0.15, focus: 0.25 },
};
const AXIS_LABELS = {
  sharpness: "Sharpness",
  expression: "Expression",
  composition: "Composition",
  exposure: "Exposure",
  focus: "Subject focus",
};

// Six fake results — raw axis scores 0-10
const RAW_RESULTS = [
  {
    id: 1,
    raw: { sharpness: 9.2, expression: 9.6, composition: 8.4, exposure: 8.9, focus: 9.1 },
    note: "Everyone is looking — and her laugh lands a half-second before the others react.",
    tag: "IMG_4821.HEIC",
    captured: "16:42",
  },
  {
    id: 2,
    raw: { sharpness: 9.0, expression: 7.8, composition: 8.9, exposure: 8.5, focus: 8.8 },
    note: "Crisp and balanced, but the youngest is mid-blink.",
    tag: "IMG_4822.HEIC",
    captured: "16:42",
  },
  {
    id: 3,
    raw: { sharpness: 8.4, expression: 8.2, composition: 7.6, exposure: 8.7, focus: 8.0 },
    note: "Warm light, candid energy — frame is slightly tilted.",
    tag: "IMG_4823.HEIC",
    captured: "16:43",
  },
  {
    id: 4,
    raw: { sharpness: 7.1, expression: 7.9, composition: 8.0, exposure: 7.8, focus: 7.4 },
    note: "Nice grouping, but soft on the front subject.",
    tag: "IMG_4824.HEIC",
    captured: "16:43",
  },
  {
    id: 5,
    raw: { sharpness: 8.6, expression: 6.4, composition: 7.0, exposure: 7.5, focus: 7.8 },
    note: "Sharp, but two faces are turned away.",
    tag: "IMG_4825.HEIC",
    captured: "16:43",
  },
  {
    id: 6,
    raw: { sharpness: 6.2, expression: 7.0, composition: 6.8, exposure: 6.4, focus: 6.6 },
    note: "Motion blur on the left edge.",
    tag: "IMG_4826.HEIC",
    captured: "16:44",
  },
];

function computeRanked(profile) {
  const w = WEIGHTS[profile];
  const scored = RAW_RESULTS.map((r) => {
    let total = 0;
    const contribs = {};
    for (const axis of Object.keys(w)) {
      const c = r.raw[axis] * w[axis]; // contribution out of 10*weight
      contribs[axis] = c;
      total += c;
    }
    return { ...r, contribs, total };
  });
  scored.sort((a, b) => b.total - a.total);
  return scored;
}

// ---------- Screen 1: Upload ----------
function UploadScreen({ profile, setProfile, onStart }) {
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef(null);
  return (
    <div className="pr-app">
      <div className="pr-scroll">
        <div className="upload-screen">
          <div className="brand-row">
            <div className="brand">
              <div className="brand-mark" aria-hidden="true"></div>
              <div className="brand-name">PhotoRank</div>
            </div>
            <div className="eyebrow">v0.4</div>
          </div>

          <div className="hero-copy">
            <div className="display hero-title">
              Pick the <em>best</em> shot,<br />instantly.
            </div>
            <div className="hero-sub">
              Drop 2–20 similar photos. We score them on the things that matter for the moment.
            </div>
          </div>

          <button
            className={"drop-zone" + (dragging ? " dragging" : "")}
            onClick={() => fileRef.current && fileRef.current.click()}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); onStart(); }}
          >
            <div className="drop-stack" aria-hidden="true">
              <div className="drop-card"></div>
              <div className="drop-card"></div>
              <div className="drop-card"></div>
            </div>
            <div className="drop-title">Drop photos here</div>
            <div className="drop-hint">or tap to pick from your library · 2–20 images</div>
            <span className="drop-cta" onClick={(e) => { e.stopPropagation(); onStart(); }}>
              Choose photos
            </span>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={onStart}
            />
          </button>

          <div className="profile-section">
            <div className="section-head">
              <div className="section-title">Scoring profile</div>
              <div className="eyebrow">Pick one</div>
            </div>
            <div className="profile-grid">
              {PROFILES.map((p) => (
                <button
                  key={p.id}
                  className="profile-card"
                  aria-pressed={profile === p.id}
                  onClick={() => setProfile(p.id)}
                >
                  <div className="profile-dot" />
                  <div className="profile-name">{p.name}</div>
                  <div className="profile-desc">{p.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="footnote">Photos stay on device · nothing uploaded</div>
        </div>
      </div>
    </div>
  );
}

// ---------- Screen 2: Loading ----------
function LoadingScreen({ count, profile, onDone, onBack }) {
  const [pct, setPct] = useState(0);
  const [stageIdx, setStageIdx] = useState(0);
  const stages = ["Decoding photos", "Detecting subjects", "Scoring axes", "Ranking"];

  useEffect(() => {
    const start = performance.now();
    const total = 3200;
    let raf;
    const tick = (t) => {
      const p = Math.min(1, (t - start) / total);
      setPct(p);
      const s = Math.min(stages.length - 1, Math.floor(p * stages.length));
      setStageIdx(s);
      if (p < 1) raf = requestAnimationFrame(tick);
      else setTimeout(onDone, 320);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const processed = Math.round(pct * count);
  const profileName = PROFILES.find((p) => p.id === profile)?.name || "Custom";

  return (
    <div className="pr-app">
      <div className="pr-scroll">
        <div className="loading-screen">
          <div className="loading-top">
            <button className="loading-back" onClick={onBack}>
              <span style={{ fontSize: 14 }}>‹</span> Cancel
            </button>
            <div className="eyebrow">{profileName} profile</div>
          </div>

          <div className="loading-center">
            <div className="loading-status">
              <div className="loading-eyebrow">
                <span className="loading-dot" />
                <span className="eyebrow" style={{ color: "var(--accent)" }}>Analysing</span>
              </div>
              <div className="display loading-title">
                Reading <em>{count}</em> photos<br />for the moment that lands.
              </div>
              <div className="loading-sub">
                Scoring via PhotoRank AI — sharpness, expression, composition, exposure, focus.
              </div>
            </div>

            <div className="loading-thumbs" aria-hidden="true">
              {Array.from({ length: count }).map((_, i) => (
                <div
                  key={i}
                  className={"loading-thumb" + (i < processed ? " done" : "")}
                >
                  {i < processed && <span className="check">✓</span>}
                </div>
              ))}
            </div>
          </div>

          <div className="loading-stats">
            <div>
              <div className="loading-progress-row">
                <div className="loading-count">
                  <span className="mono">{String(processed).padStart(2, "0")}</span>
                  <span style={{ color: "var(--muted)" }}> / {String(count).padStart(2, "0")} processed</span>
                </div>
                <div className="loading-pct mono">{Math.round(pct * 100)}%</div>
              </div>
              <div className="loading-bar"><div className="loading-bar-fill" style={{ width: `${pct * 100}%` }} /></div>
            </div>
            <div className="loading-stages">
              {stages.map((s, i) => (
                <div
                  key={s}
                  className={"stage " + (i < stageIdx ? "done" : i === stageIdx ? "active" : "")}
                >
                  <div className="stage-marker">{i < stageIdx ? "✓" : ""}</div>
                  <div>{s}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- Screen 3: Results ----------
function AxisBar({ axis, raw, weight, contrib, maxContrib }) {
  // Bar width is contribution as % of max possible contribution for this axis (weight * 10),
  // so a viewer can see what fraction of that axis's potential the photo captured.
  // The "cap" guide shows weight*10 relative to maxContrib of the heaviest axis.
  const widthPct = (contrib / maxContrib) * 100;
  const capPct = (weight * 10 / maxContrib) * 100;
  return (
    <div className="axis">
      <div className="axis-name">
        {AXIS_LABELS[axis]}
        <span className="weight">{Math.round(weight * 100)}%</span>
      </div>
      <div className="axis-vals">
        <span className="raw">{raw.toFixed(1)}</span>
        <span className="sep">×</span>
        <span>{weight.toFixed(2)}</span>
        <span className="sep">=</span>
        <span className="contrib">{contrib.toFixed(2)}</span>
      </div>
      <div className="axis-bar-wrap">
        <div className="axis-bar-cap" style={{ width: `${capPct}%` }} />
        <div className="axis-bar" style={{ width: `${widthPct}%` }} />
      </div>
    </div>
  );
}

function RankCard({ photo, rank, profile, open, onToggle }) {
  const w = WEIGHTS[profile];
  const axes = Object.keys(w);
  // Max contribution any axis can deliver = max(weight) * 10. Use this as the bar denominator
  // so heavier axes naturally take more bar width — preserving the "bar width = contribution" rule.
  const maxContrib = Math.max(...axes.map((a) => w[a] * 10));

  const thumbCls = `rank-thumb rank-thumb-${photo.id}`;
  return (
    <div className={"rank-card" + (open ? " open" : "")}>
      <button className="rank-row" onClick={onToggle} aria-expanded={open}>
        <div className="rank-pos">{String(rank).padStart(2, "0")}</div>
        <div className={thumbCls}>
          <div className="placeholder-fill" />
        </div>
        <div className="rank-text">
          <div className="rank-blurb">{photo.note}</div>
          <div className="rank-sub">{photo.tag} · {photo.captured}</div>
        </div>
        <div className="rank-right">
          <div className="rank-score">{photo.total.toFixed(1)}<span className="denom">/10</span></div>
          <div className="chev">⌄</div>
        </div>
      </button>
      {open && (
        <div className="breakdown">
          <div className="breakdown-head">
            <div className="eyebrow">Score breakdown</div>
            <div className="breakdown-total mono">Σ {photo.total.toFixed(2)}</div>
          </div>
          {axes.map((axis) => (
            <AxisBar
              key={axis}
              axis={axis}
              raw={photo.raw[axis]}
              weight={w[axis]}
              contrib={photo.contribs[axis]}
              maxContrib={maxContrib}
            />
          ))}
          <div className="breakdown-foot">
            <div className="breakdown-key"><span className="dot" />Bar width = weight × raw score</div>
            <div className="breakdown-key">Dashed = axis max</div>
          </div>
        </div>
      )}
    </div>
  );
}

function ResultsScreen({ profile, count, onRestart }) {
  const ranked = React.useMemo(() => computeRanked(profile), [profile]);
  const winner = ranked[0];
  const rest = ranked.slice(1, count);
  const [openId, setOpenId] = useState(rest[0]?.id ?? null);
  const [scrolled, setScrolled] = useState(false);
  const scrollRef = useRef(null);

  const profileName = PROFILES.find((p) => p.id === profile)?.name || "Custom";
  const wList = WEIGHTS[profile];
  const axes = Object.keys(wList);
  const heroMaxContrib = Math.max(...axes.map((a) => wList[a] * 10));

  return (
    <div className="pr-app">
      <div
        className="pr-scroll"
        ref={scrollRef}
        onScroll={(e) => setScrolled(e.target.scrollTop > 6)}
      >
        <div className="results-screen">
          <div className={"results-top" + (scrolled ? " scrolled" : "")}>
            <button className="icon-btn" onClick={onRestart}>
              <span style={{ fontSize: 14 }}>‹</span> New batch
            </button>
            <div className="results-meta">{profileName} · {count} photos</div>
            <button className="icon-btn" aria-label="More">⋯</button>
          </div>

          <div className="hero-card">
            <div className="hero-photo">
              <div className="placeholder-fill" />
              <div className="hero-rank-badge">
                <span className="rank-num">№1</span>
                <span>Best shot</span>
              </div>
              <div className="placeholder-tag">{winner.tag}</div>
            </div>
            <div className="hero-body">
              <div className="hero-score-row">
                <div className="display hero-score">
                  {winner.total.toFixed(1)}<span className="denom">/10</span>
                </div>
                <div className="hero-label-stack">
                  <div className="eyebrow">Final score</div>
                  <div className="label-main">{profileName} profile</div>
                </div>
              </div>
              <div className="hero-note">
                {winner.note}
                <span className="by">PhotoRank · {profileName} profile</span>
              </div>
            </div>
          </div>

          <div className="ranked-list-head">
            <div className="title">Runners-up</div>
            <div className="eyebrow">Tap to expand</div>
          </div>
          <div className="ranked-list">
            {rest.map((p, i) => (
              <RankCard
                key={p.id}
                photo={p}
                rank={i + 2}
                profile={profile}
                open={openId === p.id}
                onToggle={() => setOpenId(openId === p.id ? null : p.id)}
              />
            ))}
          </div>

          <div className="results-footer">
            <button className="btn-secondary" onClick={onRestart}>Rerun</button>
            <button className="btn-primary">Save winner</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------- Root ----------
function App() {
  const [screen, setScreen] = useState("upload"); // upload | loading | results
  const [profile, setProfile] = useState("family");
  const [count] = useState(6);

  const screenEl =
    screen === "upload" ? (
      <UploadScreen profile={profile} setProfile={setProfile} onStart={() => setScreen("loading")} />
    ) : screen === "loading" ? (
      <LoadingScreen count={count} profile={profile} onBack={() => setScreen("upload")} onDone={() => setScreen("results")} />
    ) : (
      <ResultsScreen profile={profile} count={count} onRestart={() => setScreen("upload")} />
    );

  return (
    <>
      <IOSDevice>
        {screenEl}
      </IOSDevice>
      <div className="proto-nav" role="tablist" aria-label="Prototype screens">
        {[
          { id: "upload", label: "Upload" },
          { id: "loading", label: "Loading" },
          { id: "results", label: "Results" },
        ].map((s) => (
          <button
            key={s.id}
            className={screen === s.id ? "active" : ""}
            onClick={() => setScreen(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);