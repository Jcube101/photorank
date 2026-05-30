// Screen 2 — Loading. Compresses the chosen photos, posts them to /rank, and
// animates the pipeline stages while the single request is in flight.
//
// The stages mirror the real backend (ingest → score_tech → score_vision →
// rank). Since /rank is one blocking call, the progress bar eases toward ~92%
// while waiting and completes when the response lands. The "Scoring axes"
// (Gemini) stage is dropped when we expect burst mode (≤6 photos) — purely a
// display cue; the server still makes the real burst/set decision from EXIF.

import { useEffect, useRef, useState } from "react";
import { profileName } from "../config.js";
import { compressImage } from "../compress.js";
import { rankPhotos } from "../api.js";

export default function LoadingScreen({ files, profile, onDone, onError, onBack }) {
  const count = files.length;
  const isBurstGuess = count <= 6;
  const stages = isBurstGuess
    ? ["Decoding photos", "Detecting subjects", "Ranking"]
    : ["Decoding photos", "Detecting subjects", "Scoring axes", "Ranking"];

  const [pct, setPct] = useState(0.02);
  const [compressedCount, setCompressedCount] = useState(0);
  const [previews, setPreviews] = useState([]); // {name, url|null} as compressed
  const [finished, setFinished] = useState(false);

  const finishedRef = useRef(false);
  const abortRef = useRef(null);

  // ── Pipeline: compress → rank ──────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let handedOff = false;
    const createdUrls = [];
    const controller = new AbortController();
    abortRef.current = controller;

    (async () => {
      try {
        const compressed = [];
        const previewMap = new Map();
        for (const f of files) {
          const r = await compressImage(f);
          if (cancelled) {
            if (r.previewUrl) URL.revokeObjectURL(r.previewUrl);
            return;
          }
          compressed.push(r.file);
          if (r.previewUrl) {
            previewMap.set(r.name, r.previewUrl);
            createdUrls.push(r.previewUrl);
          }
          setCompressedCount((c) => c + 1);
          setPreviews((p) => [...p, { name: r.name, url: r.previewUrl }]);
        }

        const result = await rankPhotos(compressed, profile, {
          signal: controller.signal,
        });
        if (cancelled) return;

        finishedRef.current = true;
        setFinished(true);
        handedOff = true;
        // Let the bar visibly reach 100% before swapping screens.
        setTimeout(() => {
          if (!cancelled) onDone(result, previewMap);
        }, 360);
      } catch (err) {
        if (cancelled || err?.name === "AbortError") return;
        onError(err);
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      // Only revoke previews we still own — once handed off, the results
      // screen is responsible for them.
      if (!handedOff) createdUrls.forEach((u) => URL.revokeObjectURL(u));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Progress animation ─────────────────────────────────────────────────
  useEffect(() => {
    let raf;
    const tick = () => {
      setPct((p) => {
        const target = finishedRef.current ? 1 : 0.92;
        const next = p + (target - p) * 0.035;
        return next > 0.999 ? 1 : next;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const stageIdx = Math.min(stages.length - 1, Math.floor(pct * stages.length));
  const processed = finished ? count : compressedCount;
  const profLabel = profileName(profile);

  return (
    <div className="pr-app">
      <div className="pr-scroll">
        <div className="loading-screen">
          <div className="loading-top">
            <button className="loading-back" onClick={onBack}>
              <span style={{ fontSize: 14 }}>‹</span> Cancel
            </button>
            <div className="eyebrow">{profLabel} profile</div>
          </div>

          <div className="loading-center">
            <div className="loading-status">
              <div className="loading-eyebrow">
                <span className="loading-dot" />
                <span className="eyebrow" style={{ color: "var(--accent)" }}>
                  Analysing
                </span>
              </div>
              <div className="display loading-title">
                Reading <em>{count}</em> photos
                <br />
                for the moment that lands.
              </div>
              <div className="loading-sub">
                Scoring via PhotoRank AI — sharpness, expression, composition,
                exposure, focus.
              </div>
            </div>

            <div className="loading-thumbs" aria-hidden="true">
              {Array.from({ length: count }).map((_, i) => {
                const item = previews[i];
                const done = i < processed;
                return (
                  <div
                    key={i}
                    className={
                      "loading-thumb" +
                      (item?.url ? "" : " placeholder") +
                      (done ? " done" : "")
                    }
                  >
                    {item?.url && <img src={item.url} alt="" />}
                    {done && <span className="check">✓</span>}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="loading-stats">
            <div>
              <div className="loading-progress-row">
                <div className="loading-count">
                  <span className="mono">{String(processed).padStart(2, "0")}</span>
                  <span style={{ color: "var(--muted)" }}>
                    {" "}
                    / {String(count).padStart(2, "0")} processed
                  </span>
                </div>
                <div className="loading-pct mono">{Math.round(pct * 100)}%</div>
              </div>
              <div className="loading-bar">
                <div
                  className="loading-bar-fill"
                  style={{ width: `${pct * 100}%` }}
                />
              </div>
            </div>
            <div className="loading-stages">
              {stages.map((s, i) => {
                const done = finished || i < stageIdx;
                const active = !finished && i === stageIdx;
                return (
                  <div
                    key={s}
                    className={"stage " + (done ? "done" : active ? "active" : "")}
                  >
                    <div className="stage-marker">{done ? "✓" : ""}</div>
                    <div>{s}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
