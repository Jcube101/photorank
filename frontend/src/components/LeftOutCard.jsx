// A photo that was picked but not ranked: its thumbnail, name and the reason
// (lib/leftOut.js). Listed under "Left out" so every picked photo is on screen.

import Photo from "./Photo.jsx";
import { leftOutReason } from "../lib/leftOut.js";

export default function LeftOutCard({ entry, previewUrl, threshold, onOpen }) {
  return (
    <div className="rank-card left-out-card">
      <div className="rank-row">
        <div className="rank-pos">—</div>
        <div className="rank-thumb">
          <Photo src={previewUrl} />
        </div>
        <div className="rank-text">
          <div className="rank-blurb">{leftOutReason(entry, threshold)}</div>
          <div className="rank-sub">{entry.photo_id}</div>
        </div>
      </div>
      {onOpen && (
        <button
          className="thumb-open"
          onClick={onOpen}
          aria-label={`View ${entry.photo_id} full screen`}
        />
      )}
    </div>
  );
}
