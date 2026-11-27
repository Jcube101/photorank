// PhotoRank PWA — root state machine: upload → loading → results, with a
// graceful error/offline state. Owns the uploaded files for the in-flight
// batch and the preview object URLs handed back by the loading screen (revoked
// on reset so nothing lingers — photos stay on device, nothing is stored).

import { useCallback, useEffect, useRef, useState } from "react";
import UploadScreen from "./screens/UploadScreen.jsx";
import LoadingScreen from "./screens/LoadingScreen.jsx";
import ResultsScreen from "./screens/ResultsScreen.jsx";
import MessageScreen from "./components/MessageScreen.jsx";
import { usePwaInstall } from "./usePwaInstall.js";
import { newerBuildDeployed } from "./lib/freshness.js";

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
  // Map<photo_id, File>: the picked originals, for the full-screen viewer. In
  // memory only (never stored, never uploaded); gone on reset or restore.
  const [originals, setOriginals] = useState(null);
  const [error, setError] = useState(null);

  const online = useOnline();
  const install = usePwaInstall();

  // Pick up a new deploy. Android keeps an installed PWA alive in memory, so
  // reopening it doesn't reload it (lib/freshness.js). When the app comes back
  // to the front, or returns to the upload screen, check whether a newer build
  // is deployed, and reload only where nothing is lost: the upload screen with
  // no photos picked. Elsewhere it waits: "New batch" lands on that screen.
  const screenRef = useRef(screen);
  screenRef.current = screen;
  const pickedRef = useRef(0); // photos picked on the upload screen
  const staleRef = useRef(false);
  const reloadIfStale = useCallback(async () => {
    if (!staleRef.current) staleRef.current = await newerBuildDeployed();
    if (staleRef.current && screenRef.current === "upload" && pickedRef.current === 0) {
      window.location.reload();
    }
  }, []);
  useEffect(() => {
    const onShow = () => document.visibilityState === "visible" && reloadIfStale();
    document.addEventListener("visibilitychange", onShow);
    return () => document.removeEventListener("visibilitychange", onShow);
  }, [reloadIfStale]);
  useEffect(() => {
    if (screen === "upload") reloadIfStale();
  }, [screen, reloadIfStale]);

  // Release any previews we still hold before replacing/clearing them.
  const revokePreviews = useCallback((map) => {
    map?.forEach?.((url) => url && URL.revokeObjectURL(url));
  }, []);

  const reset = useCallback(() => {
    revokePreviews(previews);
    setPreviews(null);
    setOriginals(null);
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
    (res, previewMap, { unprepared = [], uploaded = [], originals = null } = {}) => {
      // The names the phone couldn't prepare, and the names it uploaded, so the
      // results screen can name every photo left out, including after a
      // restore (lib/leftOut.js).
      const shown = { ...res, unprepared, uploaded };
      setResult(shown);
      setPreviews(previewMap);
      setOriginals(originals);
      setScreen("results");
      install.armAfterSuccess();
      try {
        sessionStorage.setItem(RESULT_KEY, JSON.stringify(shown));
      } catch {
        // Storage full/unavailable — non-fatal, just lose session continuity.
      }
    },
    [install],
  );

  const handleError = useCallback((err) => {
    const offline = err?.kind === "offline";
    const unprepared = err?.kind === "unprepared";
    setError({
      eyebrow: offline ? "Offline" : unprepared ? "Couldn't prepare" : "Couldn't rank",
      title: offline
        ? "You're offline."
        : unprepared
          ? "Not enough photos to rank."
          : "Something went wrong.",
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
      <ResultsScreen
        result={result}
        previews={previews}
        originals={originals}
        onRestart={reset}
      />
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
        onPickedChange={(n) => (pickedRef.current = n)}
        installSlot={installSlot}
        offline={!online}
      />
    );
  }

  return <main>{body}</main>;
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
