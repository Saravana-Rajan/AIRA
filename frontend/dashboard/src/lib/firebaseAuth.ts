// Google sign-in for the Live page, on the Firebase web SDK. Loaded on demand (useMonitor) and only when the
// build has the Firebase web config, so the first paint and the other pages never wait for it. The Firebase
// app is shared with Firestore (lib/firebase) when that already started, else started here from the same
// VITE_FIREBASE_* config. The ID token this hands out goes to aira-live's /monitor as {t:"auth", idToken} and
// nowhere else: never the URL, storage or the console.
import { getApp, getApps, initializeApp } from "firebase/app";
import {
  getAuth, getRedirectResult, GoogleAuthProvider, onAuthStateChanged, signInWithPopup, signInWithRedirect, signOut,
  type User,
} from "firebase/auth";
import { firebaseOptions } from "./firebaseConfig";
import { signInWithFallback, type ViewerAuth, type ViewerUser } from "./viewerAuth";

const env = import.meta.env as Record<string, unknown>;

const toViewer = (u: User | null): ViewerUser | null =>
  u ? { uid: u.uid, email: u.email ?? null, name: u.displayName ?? null } : null;

export function createViewerAuth(): ViewerAuth {
  const app = getApps().length ? getApp() : initializeApp(firebaseOptions(env));
  const auth = getAuth(app);
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  // A redirect sign-in lands back here: finishing it fires onAuthStateChanged; a failure is reported by signIn's caller
  // next time, so it is ignored here.
  getRedirectResult(auth).catch(() => undefined);
  const userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent;

  return {
    subscribe(cb) {
      return onAuthStateChanged(auth, (u) => cb(toViewer(u)));
    },
    async signIn() {
      await signInWithFallback(() => signInWithPopup(auth, provider), () => signInWithRedirect(auth, provider), userAgent);
    },
    async signOut() {
      await signOut(auth);
    },
    async idToken(force = false) {
      const u = auth.currentUser;
      if (!u) throw new Error("signed out");
      return u.getIdToken(force);
    },
  };
}
