"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Eye, EyeOff } from "lucide-react";
import { API_BASE_URL } from "@/lib/api";
import { LoginBrand } from "@/components/LoginBrand";
import { MIN_PASSWORD_LENGTH, USERNAME_HINT, passwordLength, usernameProblem } from "@/lib/signup";
import { clearEvidenceCache } from "@/lib/evidenceCache";

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SEND_FAILED = "Couldn't create your account. Check your connection and try again.";

type Field = "username" | "email" | "password";
type Errors = Partial<Record<Field, string>>;

// In the order they appear, so the summary reads top to bottom.
const FIELDS: { field: Field; id: string }[] = [
  { field: "username", id: "signup-username" },
  { field: "email", id: "signup-email" },
  { field: "password", id: "signup-password" },
];

type Draft = {
  username: string;
  email: string;
  password: string;
  /** The honeypot: people never see it, form-filling bots do. */
  website: string;
};

function check(d: Draft): Errors {
  const errors: Errors = {};
  const username = usernameProblem(d.username);
  if (username) errors.username = username;
  if (!d.email.trim()) errors.email = "Enter your email address.";
  else if (!EMAIL_SHAPE.test(d.email.trim())) errors.email = "That doesn't look like an email address.";
  if (passwordLength(d.password) < MIN_PASSWORD_LENGTH) errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  return errors;
}

function isField(value: unknown): value is Field {
  return value === "username" || value === "email" || value === "password";
}

/** Anyone can make an account here and is in at once as a "user"
 * (apps/bot-service/app/api/signup.py). `open` is false when an admin has
 * switched signups off. */
export function SignupForm({ open }: { open: boolean }) {
  const [draft, setDraft] = useState<Draft>({ username: "", email: "", password: "", website: "" });
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  // 403/429/503/network: not something to fix in one box.
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const summaryRef = useRef<HTMLDivElement | null>(null);

  const problems = FIELDS.filter(({ field }) => errors[field]);

  // Focus the summary after each failed attempt, so a screen reader
  // announces it and the keyboard starts from there.
  useEffect(() => {
    if (attempt) summaryRef.current?.focus();
  }, [attempt]);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    // Fixing a field clears its message; new ones only appear on submit.
    if (key in errors) setErrors((e) => ({ ...e, [key]: undefined }));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFailure(null);
    const found = check(draft);
    setErrors(found);
    if (Object.keys(found).length) {
      setAttempt((n) => n + 1);
      return;
    }
    setBusy(true);
    try {
      // Straight from the browser, like sign-in: the response sets the
      // session cookie, and the API limits tries per visitor address.
      const res = await fetch(`${API_BASE_URL}/auth/signup`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: draft.username.trim(),
          email: draft.email.trim(),
          password: draft.password,
          website: draft.website,
        }),
      });
      const body = await res.json().catch(() => null);
      const detail = typeof body?.detail === "string" ? body.detail : null;
      if (res.ok) {
        // Signed in now. Whoever used this browser before may have left
        // case photos cached; sign-out clears them too (UserMenu.tsx).
        await clearEvidenceCache().catch(() => undefined);
        window.location.assign("/");
        return;
      }
      if (detail && isField(body?.field)) {
        setErrors({ [body.field]: detail });
        setAttempt((n) => n + 1);
      } else {
        setFailure(detail || SEND_FAILED);
      }
    } catch {
      setFailure(SEND_FAILED);
    }
    setBusy(false);
  };

  const errorId = (field: Field) => (errors[field] ? `${field}-error` : undefined);
  const fieldError = (field: Field) =>
    errors[field] ? (
      <span id={`${field}-error`} className="field-error">
        {errors[field]}
      </span>
    ) : null;

  if (!open) {
    return (
      <div className="login-screen">
        <div className="login-card card-glass">
          <LoginBrand />
          <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Signups are closed</h1>
          <p className="setup-note" style={{ marginBottom: 18 }}>
            New accounts can&apos;t be made right now. Ask the Carlink admin to invite you.
          </p>
          <Link href="/login" className="btn-secondary-modern" style={{ width: "100%", justifyContent: "center" }}>
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="login-screen">
      <form className="login-card card-glass" onSubmit={submit} noValidate>
        <LoginBrand />

        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Create an account</h1>
        <p className="setup-note" style={{ marginBottom: 18 }}>It takes a minute. You&apos;re in as soon as it&apos;s made.</p>

        {problems.length > 0 && (
          <div ref={summaryRef} className="error-summary" role="alert" tabIndex={-1} aria-labelledby="signup-problem">
            <div id="signup-problem" className="error-summary-title">
              There&apos;s a problem
            </div>
            <ul>
              {problems.map(({ field, id }) => (
                <li key={field}>
                  <a
                    href={`#${id}`}
                    onClick={(e) => {
                      // Focus the field without putting #id in the address.
                      e.preventDefault();
                      document.getElementById(id)?.focus();
                    }}
                  >
                    {errors[field]}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        <label className="auth-field">
          <span className="detail-field-label">Username</span>
          <input
            id="signup-username"
            className="settings-input"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            required
            value={draft.username}
            onChange={(e) => set("username", e.target.value)}
            aria-invalid={Boolean(errors.username)}
            aria-describedby={["username-hint", errorId("username")].filter(Boolean).join(" ")}
          />
          <span id="username-hint" className="field-hint">
            {USERNAME_HINT}
          </span>
          {fieldError("username")}
        </label>

        <label className="auth-field">
          <span className="detail-field-label">Email</span>
          <input
            id="signup-email"
            className="settings-input"
            type="email"
            autoComplete="email"
            required
            value={draft.email}
            onChange={(e) => set("email", e.target.value)}
            aria-invalid={Boolean(errors.email)}
            aria-describedby={errorId("email")}
          />
          {fieldError("email")}
        </label>

        <label className="auth-field">
          <span className="detail-field-label">Password</span>
          <div style={{ position: "relative" }}>
            <input
              id="signup-password"
              className="settings-input"
              style={{ paddingRight: 38 }}
              type={showPassword ? "text" : "password"}
              autoComplete="new-password"
              required
              value={draft.password}
              onChange={(e) => set("password", e.target.value)}
              aria-invalid={Boolean(errors.password)}
              aria-describedby={["password-hint", errorId("password")].filter(Boolean).join(" ")}
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
          <span id="password-hint" className="field-hint">
            At least {MIN_PASSWORD_LENGTH} characters.
          </span>
          {fieldError("password")}
        </label>

        {/* Honeypot: off screen rather than type=hidden, which bots skip. */}
        <div className="signup-hp" aria-hidden="true">
          <label>
            Website
            <input
              type="text"
              name="website"
              tabIndex={-1}
              autoComplete="off"
              value={draft.website}
              onChange={(e) => setDraft((d) => ({ ...d, website: e.target.value }))}
            />
          </label>
        </div>

        {failure && (
          <div className="setup-error" role="alert" style={{ marginTop: 12 }}>
            {failure}
          </div>
        )}

        <button
          type="submit"
          className="btn-primary-modern"
          style={{ width: "100%", justifyContent: "center", marginTop: 16 }}
          disabled={busy}
        >
          {busy ? "Creating your account…" : "Create account"}
        </button>

        <p className="setup-note" style={{ margin: "12px 0 0", textAlign: "center" }}>
          By creating an account you agree to the{" "}
          <Link href="/privacy" target="_blank" rel="noopener" className="setup-help-link">
            privacy notice
          </Link>
          .
        </p>

        <div className="login-divider" />
        <p className="setup-note" style={{ margin: 0, textAlign: "center" }}>
          Already have an account?{" "}
          <Link href="/login" className="setup-help-link">
            Sign in
          </Link>
        </p>
      </form>
    </div>
  );
}
