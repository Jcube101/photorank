// Add-to-Home-Screen prompt logic.
//
// Chrome/Android fire `beforeinstallprompt`, which we stash and replay when the
// user accepts. iOS Safari never fires it, so we detect iOS + non-standalone
// and surface a short manual hint instead. Either way the prompt is only armed
// after the first successful ranking (call `armAfterSuccess`) and is dismissed
// for the session once shown.

import { useCallback, useEffect, useState } from "react";

const DISMISS_KEY = "pr_install_dismissed";

function isIos() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}
function isStandalone() {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    window.navigator.standalone === true
  );
}

export function usePwaInstall() {
  const [deferred, setDeferred] = useState(null);
  const [armed, setArmed] = useState(false); // first success has happened
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISS_KEY) === "1",
  );

  useEffect(() => {
    const onPrompt = (e) => {
      e.preventDefault();
      setDeferred(e);
    };
    const onInstalled = () => {
      setDeferred(null);
      setDismissed(true);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  const armAfterSuccess = useCallback(() => setArmed(true), []);

  const dismiss = useCallback(() => {
    setDismissed(true);
    localStorage.setItem(DISMISS_KEY, "1");
  }, []);

  const accept = useCallback(async () => {
    if (!deferred) return;
    deferred.prompt();
    await deferred.userChoice.catch(() => {});
    setDeferred(null);
    dismiss();
  }, [deferred, dismiss]);

  const ios = isIos() && !isStandalone();
  const canPrompt =
    armed && !dismissed && !isStandalone() && (deferred != null || ios);

  return {
    canPrompt,
    isIos: ios,
    accept: deferred ? accept : null, // null on iOS → show manual hint, no button
    dismiss,
    armAfterSuccess,
  };
}
