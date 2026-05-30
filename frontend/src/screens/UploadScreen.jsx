// Screen 1 — Upload. Brand, tagline, drag/drop (or tap) photo picker with
// 2–20 validation, profile selector, and the primary "Rank" action.

import { useEffect, useRef, useState } from "react";
import {
  PROFILES,
  MIN_FILES,
  MAX_FILES,
  ACCEPTED_MIME,
} from "../config.js";

const ACCEPT_ATTR = ACCEPTED_MIME.join(",") + ",.heic,.heif";

function validate(files) {
  if (files.length === 0) return null; // nothing chosen yet — no error
  if (files.length < MIN_FILES) return `Pick at least ${MIN_FILES} photos to compare.`;
  if (files.length > MAX_FILES) return `That's ${files.length} photos — the max is ${MAX_FILES}. Remove a few.`;
  return null;
}

export default function UploadScreen({ profile, setProfile, onStart, installSlot, offline }) {
  const [files, setFiles] = useState([]);
  const [dragging, setDragging] = useState(false);
  const [previews, setPreviews] = useState([]); // object URLs for the summary
  const fileRef = useRef(null);

  // Build/replace lightweight previews for the chosen files. Revoked on change
  // and unmount so we never leak object URLs.
  useEffect(() => {
    const urls = files.map((f) => URL.createObjectURL(f));
    setPreviews(urls);
    return () => urls.forEach((u) => URL.revokeObjectURL(u));
  }, [files]);

  const error = validate(files);
  const canRank = files.length >= MIN_FILES && files.length <= MAX_FILES;

  function pick(list) {
    const arr = Array.from(list).filter((f) => {
      // Accept by MIME when present, else by extension (HEIC often has empty type).
      if (f.type && ACCEPTED_MIME.includes(f.type)) return true;
      return /\.(jpe?g|png|webp|heic|heif)$/i.test(f.name);
    });
    if (arr.length) setFiles(arr);
  }

  return (
    <div className="pr-app">
      <div className="pr-scroll">
        {offline && (
          <div className="banner banner-offline">
            <span className="dot" aria-hidden="true" />
            <span>You're offline — connect to the internet to score photos.</span>
          </div>
        )}
        <div className="upload-screen">
          <div className="brand-row">
            <div className="brand">
              <div className="brand-mark" aria-hidden="true" />
              <div className="brand-name">PhotoRank</div>
            </div>
            <div className="eyebrow">v0.4</div>
          </div>

          <div className="hero-copy">
            <div className="display hero-title">
              Pick the <em>best</em> shot,
              <br />
              instantly.
            </div>
            <div className="hero-sub">
              Drop 2–20 similar photos. We score them on the things that matter
              for the moment.
            </div>
          </div>

          <button
            type="button"
            className={
              "drop-zone" +
              (dragging ? " dragging" : "") +
              (files.length ? " has-files" : "")
            }
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              pick(e.dataTransfer.files);
            }}
          >
            {files.length === 0 ? (
              <>
                <div className="drop-stack" aria-hidden="true">
                  <div className="drop-card" />
                  <div className="drop-card" />
                  <div className="drop-card" />
                </div>
                <div className="drop-title">Drop photos here</div>
                <div className="drop-hint">
                  or tap to pick from your library · 2–20 images
                </div>
                <span className="drop-cta">Choose photos</span>
              </>
            ) : (
              <>
                <div className="selected-count">
                  {files.length} photo{files.length === 1 ? "" : "s"} selected
                </div>
                <div className="selected-thumbs" aria-hidden="true">
                  {previews.slice(0, 8).map((u, i) => (
                    <img
                      key={i}
                      className="selected-thumb"
                      src={u}
                      alt=""
                      onError={(e) => {
                        e.currentTarget.style.visibility = "hidden";
                      }}
                    />
                  ))}
                  {files.length > 8 && (
                    <span className="selected-thumb more">+{files.length - 8}</span>
                  )}
                </div>
                <div className="drop-hint">Tap to choose a different set</div>
              </>
            )}
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT_ATTR}
              multiple
              hidden
              onChange={(e) => pick(e.target.files)}
            />
          </button>

          {error && (
            <div className="inline-error" role="alert">
              <span className="x" aria-hidden="true">
                ⚠
              </span>
              <span>{error}</span>
            </div>
          )}

          <div className="profile-section">
            <div className="section-head">
              <div className="section-title">Scoring profile</div>
              <div className="eyebrow">Pick one</div>
            </div>
            <div className="profile-grid">
              {PROFILES.map((p) => (
                <button
                  key={p.id}
                  type="button"
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

          <button
            type="button"
            className="upload-cta"
            disabled={!canRank}
            onClick={() => canRank && onStart(files)}
          >
            {canRank ? `Rank ${files.length} photos` : "Choose photos to rank"}
          </button>

          {installSlot}

          <div className="footnote">Photos stay on device · nothing stored</div>
        </div>
      </div>
    </div>
  );
}
