"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Lock } from "lucide-react";
import { API_BASE_URL, SIGNED_OUT_EVENT, type CurrentUser } from "@/lib/api";

const CHECK_EVERY_MS = 5 * 60 * 1000;
const MIN_GAP_MS = 60 * 1000;

/**
 * Notices when the session has ended (12 idle hours, a password reset, the
 * account disabled) while a page is open, and offers to sign in again on
 * the spot. Nothing on the page is lost -- this matters most halfway
 * through an intake, where the form and the photo list live in the page.
 *
 * Told by lib/api.ts (SIGNED_OUT_EVENT) when a browser-side API call is
 * refused, and checks on its own when the tab comes back into view.
 */
export function SessionGuard({ user }: { user: CurrentUser }) {
  const [signedOut, setSignedOut] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastCheck = useRef(0);

  const check = useCallback(async () => {
    if (Date.now() - lastCheck.current < MIN_GAP_MS) return;
    lastCheck.current = Date.now();
    try {
      // Plain fetch, not apiFetch: this check must not re-trigger itself.
      const res = await fetch(`${API_BASE_URL}/auth/me`, { credentials: "include", cache: "no-store" });
      if (res.status === 401) setSignedOut(true);
    } catch {
      // Offline or the API restarting: not a sign-out.
    }
  }, []);

  useEffect(() => {
    const onSignedOut = (e: Event) => {
      const reason = (e as CustomEvent<string>).detail;
      // A full load, so the layout picks up the temporary password (and the
      // server sends every other page to /account). A client navigation here
      // can race a redirect already under way and crash Next's router.
      if (reason === "password_change_required") window.location.assign("/account");
      else setSignedOut(true);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    document.addEventListener("visibilitychange", onVisible);
    const timer = window.setInterval(check, CHECK_EVERY_MS);
    return () => {
      window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(timer);
    };
  }, [check]);

  if (!signedOut) return null;

  const signBackIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: user.email, password }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        setError(typeof body?.detail === "string" ? body.detail : `Sign-in failed (${res.status})`);
        return;
      }
      setPassword("");
      setSignedOut(false);
      if (body?.must_change_password) window.location.assign("/account");
    } catch {
      setError("Couldn't reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="session-guard-title">
      <form className="modal-card" onSubmit={signBackIn}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
          <Lock style={{ width: 20, height: 20, color: "var(--accent-primary)" }} />
          <h3 id="session-guard-title" style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>
            You&apos;ve been signed out
          </h3>
        </div>
        <p className="setup-note">
          Sign in again to carry on. Nothing on this page is lost — after signing in, retry whatever didn&apos;t
          save.
        </p>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span className="detail-field-label">Email</span>
          <input className="settings-input" value={user.email} readOnly />
        </label>
        <label style={{ display: "block", marginBottom: 12 }}>
          <span className="detail-field-label">Password</span>
          <input
            className="settings-input"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="setup-error">{error}</div>}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
          <a href="/login" className="setup-help-link">
            Sign in as someone else
          </a>
          <button type="submit" className="btn-primary-modern" disabled={busy || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}
