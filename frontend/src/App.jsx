// PhotoRank PWA — root state machine: upload → loading → results, with a
// graceful error/offline state. Owns the uploaded files for the in-flight
// batch and the preview object URLs handed back by the loading screen (revoked
// on reset so nothing lingers — photos stay on device, nothing is stored).

import { useCallback, useEffect, useState } from "react";
import UploadScreen from "./screens/UploadScreen.jsx";
import LoadingScreen from "./screens/LoadingScreen.jsx";
import ResultsScreen from "./screens/ResultsScreen.jsx";
import MessageScreen from "./components/MessageScreen.jsx";
import { usePwaInstall } from "./usePwaInstall.js";

// Session continuity: Android can kill the PWA process during a tab switch,
// which would otherwise drop the user back to the upload screen. We stash the
// last results JSON in sessionStorage (cleared when the tab closes) so the
// results screen survives a process restart. Only scores/notes/photo_ids are
// stored — never pixel data. Image previews are blob: URLs that cannot survive
// the kill, so a restored screen falls back to gradient placeholders.
const RESULT_KEY = "photorank_last_result";

function loadStoredResult() {
  try {
    const raw = sessionStorage.getItem(RESULT_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && Array.isArray(parsed.ranked) && parsed.ranked.length > 0) {
      return parsed;
    }
  } catch {
    // Corrupt/unavailable storage — fall through to a normal upload start.
  }
  return null;
}

function useOnline() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}

export default function App() {
  // Restore a prior session's results if the process was killed and relaunched.
  const [restored] = useState(loadStoredResult);
  const [screen, setScreen] = useState(restored ? "results" : "upload"); // upload | loading | results | error
  const [profile, setProfile] = useState("family");
  const [files, setFiles] = useState([]);
  const [result, setResult] = useState(restored);
  const [previews, setPreviews] = useState(null); // Map<photo_id, objectURL>
  const [error, setError] = useState(null);

  const online = useOnline();
  const install = usePwaInstall();

  // Release any previews we still hold before replacing/clearing them.
  const revokePreviews = useCallback((map) => {
    map?.forEach?.((url) => url && URL.revokeObjectURL(url));
  }, []);

  const reset = useCallback(() => {
    revokePreviews(previews);
    setPreviews(null);
    setResult(null);
    setFiles([]);
    setError(null);
    setScreen("upload");
    try {
      sessionStorage.removeItem(RESULT_KEY);
    } catch {
      // ignore — storage may be unavailable
    }
  }, [previews, revokePreviews]);

  const handleStart = useCallback((chosen) => {
    setFiles(chosen);
    setError(null);
    setScreen("loading");
  }, []);

  const handleDone = useCallback(
    (res, previewMap) => {
      setResult(res);
      setPreviews(previewMap);
      setScreen("results");
      install.armAfterSuccess();
      try {
        sessionStorage.setItem(RESULT_KEY, JSON.stringify(res));
      } catch {
        // Storage full/unavailable — non-fatal, just lose session continuity.
      }
    },
    [install],
  );

  const handleError = useCallback((err) => {
    const offline = err?.kind === "offline";
    setError({
      eyebrow: offline ? "Offline" : "Couldn't rank",
      title: offline ? "You're offline." : "Something went wrong.",
      body:
        err?.message ||
        "Something went wrong while ranking. Please try again.",
    });
    setScreen("error");
  }, []);

  const installSlot =
    install.canPrompt && screen === "upload" ? (
      <InstallPrompt install={install} />
    ) : null;

  let body;
  if (screen === "loading") {
    body = (
      <LoadingScreen
        files={files}
        profile={profile}
        onDone={handleDone}
        onError={handleError}
        onBack={reset}
      />
    );
  } else if (screen === "results" && result) {
    body = (
      <ResultsScreen result={result} previews={previews} onRestart={reset} />
    );
  } else if (screen === "error" && error) {
    body = (
      <MessageScreen
        eyebrow={error.eyebrow}
        title={error.title}
        body={error.body}
        actionLabel="Try again"
        onAction={reset}
      />
    );
  } else {
    body = (
      <UploadScreen
        profile={profile}
        setProfile={setProfile}
        onStart={handleStart}
        installSlot={installSlot}
        offline={!online}
      />
    );
  }

  return body;
}

function InstallPrompt({ install }) {
  return (
    <div className="install-prompt">
      <div className="ip-text">
        <div className="ip-title">Add PhotoRank to your home screen</div>
        <div className="ip-sub">
          {install.isIos
            ? "Tap the Share icon, then “Add to Home Screen.”"
            : "Launch it like an app — full screen, one tap away."}
        </div>
      </div>
      {install.accept && (
        <button className="ip-accept" onClick={install.accept}>
          Add
        </button>
      )}
      <button className="ip-dismiss" onClick={install.dismiss}>
        Not now
      </button>
    </div>
  );
}
