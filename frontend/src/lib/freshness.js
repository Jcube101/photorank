// Is this page running an old build? An installed PWA on Android can stay
// alive in memory for hours, so reopening it does not reload it, and a deploy
// never reaches it: on 2026-11-27 the owner's phone ran a bundle from before
// two deploys and showed notes those deploys had replaced (LEARNINGS).
//
// The page's entry script is /assets/index-<hash>.js, and the hash changes
// with every build. We fetch the app shell (never from cache) and check it
// still loads the same script. Anything we can't read (offline, a Cloudflare
// Access login redirect) counts as "not newer", so we never reload blindly.

/** The entry script path this page loaded, e.g. "/assets/index-abc123.js", or null. */
export function runningBuild() {
  const script = document.querySelector('script[type="module"][src*="/assets/"]');
  return script ? new URL(script.src, location.href).pathname : null;
}

/** True if the deployed app shell loads a different entry script than this page. */
export async function newerBuildDeployed() {
  const running = runningBuild();
  if (!running) return false; // dev server, or unknown
  try {
    const res = await fetch("/", { cache: "no-store", redirect: "manual" });
    if (!res.ok) return false;
    const html = await res.text();
    return /\/assets\/index-[\w-]+\.js/.test(html) && !html.includes(running);
  } catch {
    return false;
  }
}
