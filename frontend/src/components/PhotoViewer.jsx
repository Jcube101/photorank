// Full-screen photo viewer: pinch to zoom, swipe left/right through the
// photos (rank order, left-out last), swipe down or X to close. Escape closes
// and the arrow keys navigate on a laptop.
//
// Each item shows the ORIGINAL file when the app still holds it (full
// resolution, for judging focus), else the compressed preview. Originals are
// shown through object URLs made here, one at a time, and revoked when the
// slide changes: they never leave the phone, and only one full-resolution
// image is decoded at once.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

const MAX_SCALE = 5;
const SWIPE_PX = 60; // horizontal travel that moves to the next photo
const CLOSE_PX = 90; // downward travel that closes the viewer

/**
 * @param {Array<{id: string, title: string, detail: string, flag?: string,
 *                original?: File, preview?: string}>} items
 * @param {number} start   Index to open at.
 * @param {() => void} onClose
 */
export default function PhotoViewer({ items, start, onClose }) {
  const [index, setIndex] = useState(start);
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 }); // zoom and pan
  const [drag, setDrag] = useState({ x: 0, y: 0 }); // swipe in progress (scale 1)
  const item = items[index];
  const src = useSource(item);
  const dialogRef = useRef(null);
  const closeRef = useRef(null);

  const go = useCallback(
    (step) => {
      setIndex((i) => Math.min(items.length - 1, Math.max(0, i + step)));
      setView({ scale: 1, x: 0, y: 0 });
      setDrag({ x: 0, y: 0 });
    },
    [items.length],
  );

  // Focus moves into the dialog, and back to what opened it on close.
  useEffect(() => {
    const opener = document.activeElement;
    closeRef.current?.focus();
    return () => opener?.focus?.();
  }, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "Tab") {
        // A modal dialog: Tab cycles through its own buttons only.
        const buttons = [...(dialogRef.current?.querySelectorAll("button:not(:disabled)") ?? [])];
        if (!buttons.length) return;
        const at = buttons.indexOf(document.activeElement);
        const next = (at + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length;
        buttons[at < 0 ? 0 : next].focus();
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, onClose]);

  const gestures = useGestures({ view, setView, setDrag, go, onClose });

  return createPortal(
    <div
      className="viewer"
      role="dialog"
      aria-modal="true"
      aria-label="Photo viewer"
      ref={dialogRef}
      data-index={index}
    >
      <div
        className="viewer-stage"
        data-scale={view.scale.toFixed(2)}
        {...gestures}
      >
        {src.url ? (
          <img
            key={src.url}
            className="viewer-img"
            src={src.url}
            alt={item.id}
            draggable={false}
            onError={src.onError}
            style={{
              transform:
                `translate(${view.x + drag.x}px, ${view.y + drag.y}px) scale(${view.scale})`,
              opacity: drag.y > 0 ? Math.max(0.4, 1 - drag.y / 400) : 1,
            }}
          />
        ) : (
          <div className="viewer-missing">This photo can't be shown on this device.</div>
        )}
      </div>

      <div className="viewer-top">
        <div className="viewer-caption">
          <div className="viewer-title">{item.title}</div>
          <div className="viewer-detail">{item.detail}</div>
          <div className="viewer-name">{item.id}</div>
        </div>
        <button ref={closeRef} className="viewer-close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {item.flag && <div className="viewer-flag">{item.flag}</div>}

      <div className="viewer-nav">
        <button onClick={() => go(-1)} disabled={index === 0} aria-label="Previous photo">
          ‹
        </button>
        <div className="viewer-count" aria-live="polite">
          {index + 1} / {items.length}
        </div>
        <button onClick={() => go(1)} disabled={index === items.length - 1} aria-label="Next photo">
          ›
        </button>
      </div>
    </div>,
    document.body,
  );
}

// The image to show: the original, through an object URL made with the item
// and revoked when it changes, else the preview. Any source that fails to load
// (e.g. an original HEIC outside Safari) falls through to the next.
function useSource(item) {
  const originalUrl = useMemo(
    () => (item.original ? URL.createObjectURL(item.original) : null),
    [item],
  );
  useEffect(() => () => originalUrl && URL.revokeObjectURL(originalUrl), [originalUrl]);
  const [failed, setFailed] = useState(() => new Set());
  const url = [originalUrl, item.preview].find((u) => u && !failed.has(u)) ?? null;
  return { url, onError: () => setFailed((f) => new Set(f).add(url)) };
}

// Pointer gestures on the stage: two fingers pinch-zoom; one finger pans when
// zoomed, else swipes (left/right: next/previous; down: close); a double tap
// toggles zoom.
function useGestures({ view, setView, setDrag, go, onClose }) {
  const pointers = useRef(new Map());
  const gesture = useRef(null);
  const lastTap = useRef(0);

  const onPointerDown = (e) => {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    if (pts.length === 2) {
      gesture.current = {
        kind: "pinch", dist: distance(pts), scale: view.scale, x: view.x, y: view.y,
      };
      setDrag({ x: 0, y: 0 });
    } else if (pts.length === 1) {
      gesture.current = {
        kind: view.scale > 1 ? "pan" : "swipe",
        startX: e.clientX, startY: e.clientY, x: view.x, y: view.y, moved: false,
      };
    }
  };

  const onPointerMove = (e) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gesture.current;
    if (!g) return;
    const pts = [...pointers.current.values()];
    if (g.kind === "pinch" && pts.length === 2) {
      const scale = clamp((g.scale * distance(pts)) / g.dist, 1, MAX_SCALE);
      setView({ scale, x: g.x, y: g.y });
    } else if (g.kind !== "pinch" && pts.length === 1) {
      const dx = e.clientX - g.startX;
      const dy = e.clientY - g.startY;
      if (Math.abs(dx) > 6 || Math.abs(dy) > 6) g.moved = true;
      if (g.kind === "pan") setView((v) => ({ ...v, x: g.x + dx, y: g.y + dy }));
      else setDrag({ x: dx, y: Math.max(0, dy) > Math.abs(dx) ? dy : 0 });
    }
  };

  const onPointerUp = (e) => {
    const g = gesture.current;
    const p = pointers.current.get(e.pointerId);
    pointers.current.delete(e.pointerId);
    if (!g) return;
    if (g.kind === "pinch") {
      if (pointers.current.size === 0) {
        gesture.current = null;
        setView((v) => (v.scale < 1.05 ? { scale: 1, x: 0, y: 0 } : v));
      }
      return;
    }
    gesture.current = null;
    const dx = (p?.x ?? g.startX) - g.startX;
    const dy = (p?.y ?? g.startY) - g.startY;
    setDrag({ x: 0, y: 0 });
    if (!g.moved) {
      const now = Date.now();
      if (now - lastTap.current < 300) {
        setView((v) => (v.scale > 1 ? { scale: 1, x: 0, y: 0 } : { scale: 2.5, x: 0, y: 0 }));
        lastTap.current = 0;
      } else {
        lastTap.current = now;
      }
      return;
    }
    if (g.kind === "swipe") {
      if (Math.abs(dx) > SWIPE_PX && Math.abs(dx) > Math.abs(dy)) go(dx < 0 ? 1 : -1);
      else if (dy > CLOSE_PX && dy > Math.abs(dx)) onClose();
    }
  };

  return {
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel: onPointerUp,
  };
}

function distance([a, b]) {
  return Math.hypot(a.x - b.x, a.y - b.y) || 1;
}
function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}
