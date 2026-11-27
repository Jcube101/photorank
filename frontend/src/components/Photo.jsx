// A result photo, or the gradient placeholder when there is nothing to show:
// no preview URL (a result restored from sessionStorage), or a preview this
// browser can't display (an original HEIC outside Safari, uploaded as-is
// because compression failed; see compress.js).

import { useState } from "react";

export default function Photo({ src, className, alt = "" }) {
  const [failedSrc, setFailedSrc] = useState(null);
  if (!src || failedSrc === src) return <div className="placeholder-fill" />;
  return (
    <img className={className} src={src} alt={alt} onError={() => setFailedSrc(src)} />
  );
}
