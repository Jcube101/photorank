// Screen 3 + 4 — Results. Hero №1 card on top, runners-up below as cards that
// expand to the full transparent score breakdown.
//
// Profile switcher: a set-mode result scores every photo on all six axes, so
// the user can re-rank the same photos under a different profile on-device —
// instantly, no re-upload, no Gemini call (see src/lib/rerank.js). Burst-mode
// results use a different axis set and can't map onto these profiles, so the
// switcher is hidden for them.

import { useMemo, useState } from "react";
import HeroCard from "../components/HeroCard.jsx";
import RankCard from "../components/RankCard.jsx";
import {
  PROFILES,
  PROFILE_WEIGHTS,
  PROFILE_HINTED,
  profileName,
} from "../config.js";
import { rerankByProfile } from "../lib/rerank.js";

export default function ResultsScreen({ result, previews, onRestart }) {
  const canSwitch = result.mode === "set";
  const [viewProfile, setViewProfile] = useState(result.profile);

  // Re-derive the ordering from the original result whenever the viewed profile
  // changes. When it matches the run's own profile, use the server's ranking
  // verbatim; otherwise re-weight on-device.
  const ranked = useMemo(() => {
    if (!canSwitch || viewProfile === result.profile) return result.ranked;
    const weights = PROFILE_WEIGHTS[viewProfile];
    if (!weights) return result.ranked;
    return rerankByProfile(result.ranked, weights);
  }, [canSwitch, viewProfile, result]);

  const winner = ranked[0];
  const rest = ranked.slice(1);

  const profLabel = profileName(canSwitch ? viewProfile : result.profile);
  const totalPhotos = result.total_photos ?? ranked.length;

  // Travel is the only profile whose Gemini scoring differs; re-ranking into it
  // from a non-travel run is directional, not identical.
  const approximate =
    canSwitch &&
    viewProfile !== result.profile &&
    PROFILE_HINTED.has(viewProfile);

  const [openId, setOpenId] = useState(rest[0]?.photo_id ?? null);
  const [scrolled, setScrolled] = useState(false);

  const previewFor = useMemo(
    () => (id) => previews?.get?.(id) ?? null,
    [previews],
  );
  const winnerUrl = previewFor(winner.photo_id);

  async function saveWinner() {
    if (!winnerUrl) return;
    // Download from a fresh object URL built off a re-read Blob rather than the
    // live preview URL. The preview URL is owned by the document's blob store
    // and referencing it directly in an <a download> was a no-op in browser
    // Chrome; copying into a new Blob + URL gives a clean, downloadable handle.
    let freshUrl;
    try {
      const blob = await (await fetch(winnerUrl)).blob();
      freshUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = freshUrl;
      a.download = winner.photo_id || "photorank-winner.jpg";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch {
      // Last resort: point straight at the preview URL.
      const a = document.createElement("a");
      a.href = winnerUrl;
      a.download = winner.photo_id || "photorank-winner.jpg";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } finally {
      if (freshUrl) setTimeout(() => URL.revokeObjectURL(freshUrl), 10000);
    }
  }

  return (
    <div className="pr-app">
      <div
        className="pr-scroll"
        onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 6)}
      >
        <div className="results-screen">
          <div className={"results-top" + (scrolled ? " scrolled" : "")}>
            <button className="icon-btn" onClick={onRestart}>
              <span style={{ fontSize: 14 }}>‹</span> New batch
            </button>
            <div className="results-meta">
              {profLabel} · {totalPhotos} photos
            </div>
          </div>

          {canSwitch && (
            <div className="rerank">
              <div className="rerank-head">
                <div className="rerank-title">Rank for</div>
                {viewProfile !== result.profile && (
                  <button
                    className="rerank-reset"
                    onClick={() => setViewProfile(result.profile)}
                  >
                    Reset to {profileName(result.profile)}
                  </button>
                )}
              </div>
              <div className="rerank-row" role="tablist">
                {PROFILES.map((p) => (
                  <button
                    key={p.id}
                    role="tab"
                    className="rerank-chip"
                    aria-selected={viewProfile === p.id}
                    onClick={() => {
                      setViewProfile(p.id);
                      setOpenId(null);
                    }}
                  >
                    {p.name}
                  </button>
                ))}
              </div>
              {approximate && (
                <div className="rerank-note">
                  Re-ranked from this set's scores. Travel judges backgrounds
                  differently — for an exact result, run a new batch on Travel.
                </div>
              )}
            </div>
          )}

          <HeroCard photo={winner} previewUrl={winnerUrl} profileLabel={profLabel} />

          {rest.length > 0 && (
            <>
              <div className="ranked-list-head">
                <div className="title">Runners-up</div>
                <div className="eyebrow">Tap to expand</div>
              </div>
              <div className="ranked-list">
                {rest.map((p, i) => (
                  <RankCard
                    key={p.photo_id}
                    photo={p}
                    rank={i + 2}
                    previewUrl={previewFor(p.photo_id)}
                    open={openId === p.photo_id}
                    onToggle={() =>
                      setOpenId(openId === p.photo_id ? null : p.photo_id)
                    }
                  />
                ))}
              </div>
            </>
          )}

          <div className="results-footer">
            <button className="btn-secondary" onClick={onRestart}>
              Rerun
            </button>
            <button className="btn-primary" onClick={saveWinner} disabled={!winnerUrl}>
              Save winner
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
