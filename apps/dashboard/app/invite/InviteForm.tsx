"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Check, Circle, Eye, EyeOff, X } from "lucide-react";
import { API_BASE_URL, type UserRole } from "@/lib/api";
import { LoginBrand } from "@/components/LoginBrand";
import { clearEvidenceCache } from "@/lib/evidenceCache";
import {
  MIN_PASSWORD_LENGTH,
  ROLE_LABEL,
  USERNAME_HINT,
  formatSgt,
  isGuessablePasswordError,
  passwordLength,
  usernameProblem,
} from "@/lib/signup";

/** The link's token, kept for this tab only once it's out of the address
 * bar, so a reload still works. */
const TOKEN_KEY = "carlink-link-token";
const NO_SERVER = "Couldn't reach the server. Check your connection and try again.";

type LinkInfo = {
  purpose: "invite" | "reset";
  email: string;
  role: UserRole;
  name: string;
  invited_by: string;
  expires_at: string;
};

type Ended = "expired" | "used" | "invalid";

type View =
  | { kind: "loading" }
  | { kind: "form"; link: LinkInfo }
  | { kind: Ended }
  | { kind: "problem"; message: string };

function endedCode(body: unknown): Ended {
  const code = (body as { code?: unknown } | null)?.code;
  return code === "expired" || code === "used" ? code : "invalid";
}

function forgetToken() {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage blocked: nothing was kept.
  }
}

export function InviteForm() {
  const [view, setView] = useState<View>({ kind: "loading" });
  const token = useRef("");

  const lookup = async (isCurrent: () => boolean = () => true) => {
    setView({ kind: "loading" });
    let next: View;
    try {
      // From the browser, like sign-in: the API limits tries per visitor
      // address, so it must see the visitor's.
      const res = await fetch(`${API_BASE_URL}/auth/links/lookup`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.current }),
      });
      const body = await res.json().catch(() => null);
      const detail = typeof body?.detail === "string" ? body.detail : null;
      if (res.ok && (body?.purpose === "invite" || body?.purpose === "reset")) {
        next = { kind: "form", link: body as LinkInfo };
      } else if (res.status === 410) {
        next = { kind: endedCode(body) };
      } else if (res.status === 429) {
        next = { kind: "problem", message: detail || "Too many tries. Wait a minute, then try again." };
      } else if (res.status >= 500) {
        next = { kind: "problem", message: "Couldn't check your link just now. Try again in a moment." };
      } else {
        next = { kind: "invalid" };
      }
    } catch {
      next = { kind: "problem", message: NO_SERVER };
    }
    if (!isCurrent()) return;
    if (next.kind === "expired" || next.kind === "used" || next.kind === "invalid") forgetToken();
    setView(next);
  };

  useEffect(() => {
    let current = true;
    let found = window.location.hash.replace(/^#/, "").trim();
    if (found) {
      try {
        sessionStorage.setItem(TOKEN_KEY, found);
      } catch {
        // Storage blocked: the link still works, just not after a reload.
      }
      // Out of the address bar and the history, so it isn't left for the
      // next person on this device, or caught in a screenshot.
      window.history.replaceState(null, "", "/invite");
    } else {
      try {
        found = sessionStorage.getItem(TOKEN_KEY) || "";
      } catch {
        found = "";
      }
    }
    token.current = found;
    if (!found) {
      setView({ kind: "invalid" });
    } else {
      lookup(() => current);
    }
    return () => {
      current = false;
    };
    // Once, on arrival.
  }, []);

  return (
    <div className="login-screen">
      <div className="login-card card-glass wide">
        <LoginBrand />
        {view.kind === "loading" && (
          <p className="setup-note" role="status" style={{ margin: 0 }}>
            Checking your link…
          </p>
        )}
        {view.kind === "form" && (
          <PasswordForm link={view.link} token={token.current} onEnded={(code) => { forgetToken(); setView({ kind: code }); }} />
        )}
        {view.kind === "expired" && (
          <Ended title="This link has expired" text="Ask your admin for a new one." action="Back to sign in" />
        )}
        {view.kind === "used" && (
          <Ended title="This link has already been used" text="Sign in with your email and password." action="Sign in" />
        )}
        {view.kind === "invalid" && (
          <Ended
            title="This link doesn't work"
            text="It may be incomplete, or your admin may have cancelled it. Check you copied the whole link, or ask your admin for a new one."
            action="Back to sign in"
          />
        )}
        {view.kind === "problem" && (
          <>
            <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Couldn&apos;t check your link</h1>
            <div className="setup-error" role="alert" style={{ marginTop: 12 }}>
              {view.message}
            </div>
            <button type="button" className="btn-primary-modern" style={{ width: "100%", justifyContent: "center" }} onClick={() => lookup()}>
              Try again
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Ended({ title, text, action }: { title: string; text: string; action: string }) {
  return (
    <>
      <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>{title}</h1>
      <p className="setup-note" style={{ marginBottom: 18 }}>
        {text}
      </p>
      <Link href="/login" className="btn-primary-modern" style={{ width: "100%", justifyContent: "center" }}>
        {action}
      </Link>
    </>
  );
}

function PasswordForm({ link, token, onEnded }: { link: LinkInfo; token: string; onEnded: (code: Ended) => void }) {
  const invite = link.purpose === "invite";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  // The common-password check is the server's alone: unknown until it
  // refuses one, and unknown again once the password changes.
  const [guessable, setGuessable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accountExists, setAccountExists] = useState(false);

  const longEnough = passwordLength(password) >= MIN_PASSWORD_LENGTH;
  // Only once they've typed as much as the password, not on every key.
  const mismatch = confirm.length >= password.length && confirm !== password && confirm.length > 0;
  const inviter = link.invited_by?.trim() || "";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setAccountExists(false);
    const badUsername = invite ? usernameProblem(username) : null;
    if (badUsername) return setError(badUsername);
    if (!longEnough) return setError(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
    if (password !== confirm) return setError("The two passwords don't match.");

    setBusy(true);
    try {
      const res = await fetch(`${API_BASE_URL}/auth/links/accept`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, username: invite ? username.trim() : "", password }),
      });
      const body = await res.json().catch(() => null);
      const detail = typeof body?.detail === "string" ? body.detail : null;
      if (res.ok) {
        // Signed in now (the response set the cookie). Whoever used this
        // browser before may have left case photos cached; sign-out clears
        // them too (components/UserMenu.tsx).
        forgetToken();
        await clearEvidenceCache().catch(() => undefined);
        window.location.assign("/");
        return;
      }
      if (res.status === 410) {
        onEnded(endedCode(body));
        return;
      }
      if (res.status === 400 && detail) {
        setError(detail);
        if (isGuessablePasswordError(detail)) setGuessable(true);
      } else if (res.status === 409 && detail && /username/i.test(detail)) {
        setError(detail);
      } else if (res.status === 409) {
        setError(detail || "An account with this email already exists.");
        setAccountExists(true);
      } else if (res.status === 429) {
        setError(detail || "Too many tries. Wait a minute, then try again.");
      } else if (res.status === 503) {
        setError(detail || "Carlink is busy right now. Try again in a moment.");
      } else {
        setError(invite ? "Couldn't create your account. Try again." : "Couldn't save your password. Try again.");
      }
    } catch {
      setError(NO_SERVER);
    }
    setBusy(false);
  };

  return (
    <form onSubmit={submit} noValidate>
      {invite ? (
        <>
          <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Join Carlink Studio</h1>
          <p className="setup-note" style={{ marginBottom: 18 }}>
            {inviter ? `${inviter} invited you` : "You've been invited"} as {link.role === "admin" ? "an" : "a"}{" "}
            {ROLE_LABEL[link.role] ?? link.role}.
          </p>

          <label className="auth-field">
            <span className="detail-field-label">Email (locked)</span>
            <input className="settings-input" type="email" autoComplete="email" value={link.email} readOnly />
            <span className="field-hint">Not your email? Ask {inviter || "your admin"} for a new link.</span>
          </label>

          <label className="auth-field">
            <span className="detail-field-label">Username</span>
            <input
              className="settings-input"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <span className="field-hint">{USERNAME_HINT} You can sign in with it or your email.</span>
          </label>
        </>
      ) : (
        <>
          <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Choose a new password</h1>
          <p className="setup-note" style={{ marginBottom: 18 }}>
            for <strong style={{ color: "var(--text-primary)" }}>{link.email}</strong>
          </p>
          {/* So a password manager saves the new password against the right account. */}
          <input type="email" autoComplete="username" value={link.email} readOnly hidden />
        </>
      )}

      <div className="auth-field">
        <label style={{ display: "block" }}>
          <span className="detail-field-label">{invite ? "Create a password" : "New password"}</span>
          <div style={{ position: "relative" }}>
            <input
              className="settings-input password-input"
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              autoFocus={!invite}
              required
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setGuessable(false);
              }}
              aria-describedby="password-rules"
            />
            <button
              type="button"
              className="setup-reveal-btn"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              title={showPassword ? "Hide" : "Show"}
              style={{ display: "flex", alignItems: "center", justifyContent: "center", top: "calc(50% + 3px)", color: "var(--text-muted)" }}
            >
              {showPassword ? <EyeOff style={{ width: 14, height: 14 }} /> : <Eye style={{ width: 14, height: 14 }} />}
            </button>
          </div>
        </label>
        <ul id="password-rules" className="password-checklist">
          <li className={longEnough ? "ok" : ""}>
            {longEnough ? <Check aria-hidden /> : <Circle aria-hidden />}
            {MIN_PASSWORD_LENGTH} or more characters
          </li>
          {/* Only when the server refused one: the common-password check is
              off unless STRICT_PASSWORD_RULES is set (passwords.py). */}
          {guessable && (
            <li className="bad">
              <X aria-hidden />
              Not a common or easily guessed password
            </li>
          )}
        </ul>
        <span className="field-hint">Tip: 3 or 4 random words are easy to remember.</span>
      </div>

      <label className="auth-field">
        <span className="detail-field-label">Confirm password</span>
        <input
          className="settings-input"
          type={showPassword ? "text" : "password"}
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          aria-invalid={mismatch}
          aria-describedby={mismatch ? "confirm-error" : undefined}
        />
        {mismatch && (
          <span id="confirm-error" className="field-error">
            The two passwords don&apos;t match.
          </span>
        )}
      </label>

      {error && (
        <div className="setup-error" role="alert" style={{ marginTop: 4 }}>
          {error}
          {accountExists && (
            <>
              {" "}
              <Link href="/login" className="setup-help-link">
                Sign in
              </Link>
            </>
          )}
        </div>
      )}

      <button type="submit" className="btn-primary-modern" style={{ width: "100%", justifyContent: "center", marginTop: 4 }} disabled={busy}>
        {busy
          ? invite
            ? "Creating your account…"
            : "Saving…"
          : invite
            ? "Create account and sign in"
            : "Save password and sign in"}
      </button>

      <p className="setup-note" style={{ margin: "16px 0 0", textAlign: "center" }}>
        This link works once and expires on {formatSgt(link.expires_at)}.
      </p>
    </form>
  );
}
