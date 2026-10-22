"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2 } from "lucide-react";
import { API_BASE_URL, type UserRole } from "@/lib/api";
import { LoginBrand } from "@/components/LoginBrand";
import { firstName, fullMobile, maskMobile, mobileProblem } from "@/lib/signup";

const NOTE_LIMIT = 500;
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SEND_FAILED = "Couldn't send your request. Check your connection and try again.";

type Field = "name" | "email" | "mobile" | "consent";
type Errors = Partial<Record<Field, string>>;

// In the order they appear, so the summary reads top to bottom.
const FIELDS: { field: Field; id: string }[] = [
  { field: "name", id: "signup-name" },
  { field: "email", id: "signup-email" },
  { field: "mobile", id: "signup-mobile" },
  { field: "consent", id: "signup-consent" },
];

const ROLES: { role: UserRole; label: string; hint: string }[] = [
  { role: "surveyor", label: "Surveyor", hint: "works on cases" },
  { role: "admin", label: "Admin", hint: "also manages users and settings" },
];

type Draft = {
  name: string;
  email: string;
  mobile: string;
  role: UserRole;
  organisation: string;
  note: string;
  consent: boolean;
  /** The honeypot: people never see it, form-filling bots do. */
  website: string;
};

function check(d: Draft): Errors {
  const errors: Errors = {};
  if (!d.name.trim()) errors.name = "Enter your full name.";
  if (!d.email.trim()) errors.email = "Enter your email address.";
  else if (!EMAIL_SHAPE.test(d.email.trim())) errors.email = "That doesn't look like an email address.";
  const mobile = mobileProblem(d.mobile);
  if (mobile) errors.mobile = mobile;
  if (!d.consent) errors.consent = "Tick the box to send your request.";
  return errors;
}

/** Which field a 400 from the API is about, going by its wording
 * (apps/bot-service/app/api/signup.py); null puts it in the summary only. */
function fieldOf(detail: string): Field | null {
  if (/tick|consent/i.test(detail)) return "consent";
  if (/mobile|phone/i.test(detail)) return "mobile";
  if (/email/i.test(detail)) return "email";
  if (/name/i.test(detail)) return "name";
  return null;
}

export function SignupForm() {
  const [draft, setDraft] = useState<Draft>({
    name: "",
    email: "",
    mobile: "",
    role: "surveyor",
    organisation: "",
    note: "",
    consent: false,
    website: "",
  });
  const [errors, setErrors] = useState<Errors>({});
  // A 400 the API sent that isn't about one field.
  const [general, setGeneral] = useState<string | null>(null);
  // 429/503/network: not something to fix in the form.
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<{ reference: string; name: string; mobile: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const summaryRef = useRef<HTMLDivElement | null>(null);
  const sentRef = useRef<HTMLHeadingElement | null>(null);

  const problems = FIELDS.filter(({ field }) => errors[field]);
  const hasSummary = problems.length > 0 || Boolean(general);

  // Move focus to the summary after each failed attempt, so a screen
  // reader announces it and the keyboard starts from there.
  useEffect(() => {
    if (attempt) summaryRef.current?.focus();
  }, [attempt]);

  useEffect(() => {
    if (sent) sentRef.current?.focus();
  }, [sent]);

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
    // Fixing a field clears its message; new ones only appear on submit.
    if (key in errors) setErrors((e) => ({ ...e, [key]: undefined }));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFailure(null);
    setGeneral(null);
    const found = check(draft);
    setErrors(found);
    if (Object.keys(found).length) {
      setAttempt((n) => n + 1);
      return;
    }
    setBusy(true);
    try {
      // Straight from the browser, not a server action: the API limits
      // requests per visitor address, so it must see the visitor's.
      const phone = fullMobile(draft.mobile);
      const res = await fetch(`${API_BASE_URL}/auth/access-requests`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: draft.name.trim(),
          email: draft.email.trim(),
          phone,
          organisation: draft.organisation.trim(),
          requested_role: draft.role,
          note: draft.note.trim(),
          consent: draft.consent,
          website: draft.website,
        }),
      });
      const body = await res.json().catch(() => null);
      const detail = typeof body?.detail === "string" ? body.detail : null;
      if (res.ok) {
        setSent({ reference: String(body?.reference ?? ""), name: draft.name.trim(), mobile: phone });
      } else if (res.status === 400 && detail) {
        const field = fieldOf(detail);
        if (field) setErrors({ [field]: detail });
        else setGeneral(detail);
        setAttempt((n) => n + 1);
      } else if ((res.status === 429 || res.status === 503) && detail) {
        setFailure(detail);
      } else {
        setFailure(SEND_FAILED);
      }
    } catch {
      setFailure(SEND_FAILED);
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    const first = firstName(sent.name);
    return (
      <div className="login-screen">
        <div className="login-card card-glass wide">
          <LoginBrand />
          <CheckCircle2 style={{ width: 28, height: 28, color: "var(--badge-green-text)", marginBottom: 8 }} />
          <h1 ref={sentRef} tabIndex={-1} style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800, outline: "none" }}>
            Request sent
          </h1>
          <p className="setup-note" style={{ marginBottom: 18 }}>
            Thanks{first ? `, ${first}` : ""}. Your request is with the Carlink admins.
          </p>

          <div className="detail-field-label">What happens next</div>
          <ol className="auth-steps">
            <li>An admin checks your details.</li>
            <li>
              If approved, they WhatsApp you a sign-in link at <strong>{maskMobile(sent.mobile)}</strong>.
            </li>
            <li>Open it and choose your password.</li>
          </ol>

          <p className="setup-note">Nothing after 2 working days? Contact your Carlink admin directly.</p>
          {sent.reference && (
            <p className="setup-note" style={{ marginBottom: 18 }}>
              Ref <span className="auth-ref">{sent.reference}</span>
            </p>
          )}

          <Link href="/login" className="btn-primary-modern" style={{ width: "100%", justifyContent: "center" }}>
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  const errorId = (field: Field) => (errors[field] ? `${field}-error` : undefined);
  const fieldError = (field: Field) =>
    errors[field] ? (
      <span id={`${field}-error`} className="field-error">
        {errors[field]}
      </span>
    ) : null;

  return (
    <div className="login-screen">
      <form className="login-card card-glass wide" onSubmit={submit} noValidate>
        <LoginBrand />

        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Request access</h1>
        <p className="setup-note" style={{ marginBottom: 18 }}>
          Tell us who you are. An admin will review your request, usually within 1 working day.
        </p>

        {hasSummary && (
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
              {general && <li>{general}</li>}
            </ul>
          </div>
        )}

        <label className="auth-field">
          <span className="detail-field-label">Full name</span>
          <input
            id="signup-name"
            className="settings-input"
            autoComplete="name"
            autoFocus
            required
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
            aria-invalid={Boolean(errors.name)}
            aria-describedby={errorId("name")}
          />
          {fieldError("name")}
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
          <span className="detail-field-label">Mobile (for WhatsApp)</span>
          <div className="phone-field">
            {/* Gone once they type their own "+country code". */}
            {!draft.mobile.trim().startsWith("+") && <span className="phone-prefix">+65</span>}
            <input
              id="signup-mobile"
              className="settings-input"
              type="tel"
              inputMode="tel"
              autoComplete="tel-national"
              placeholder="9123 4567"
              required
              value={draft.mobile}
              onChange={(e) => set("mobile", e.target.value)}
              aria-invalid={Boolean(errors.mobile)}
              aria-describedby={["mobile-hint", errorId("mobile")].filter(Boolean).join(" ")}
            />
          </div>
          <span id="mobile-hint" className="field-hint">
            Your admin sends your sign-in link here.
          </span>
          {fieldError("mobile")}
        </label>

        <fieldset className="auth-field" style={{ border: 0, padding: 0, minWidth: 0 }}>
          <legend className="detail-field-label" style={{ padding: 0 }}>
            Role you need
          </legend>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 6 }}>
            {ROLES.map(({ role, label, hint }) => (
              <label key={role} className={`choice-option ${draft.role === role ? "selected" : ""}`}>
                <input type="radio" name="requested_role" checked={draft.role === role} onChange={() => set("role", role)} />
                <span>
                  <strong>{label}</strong> <span style={{ color: "var(--text-muted)" }}>— {hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <label className="auth-field">
          <span className="detail-field-label">Company (optional)</span>
          <input
            className="settings-input"
            autoComplete="organization"
            value={draft.organisation}
            onChange={(e) => set("organisation", e.target.value)}
          />
        </label>

        <label className="auth-field">
          <span style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span className="detail-field-label">Note for the admin (optional)</span>
            <span className="char-count" aria-live="polite">
              {draft.note.length}/{NOTE_LIMIT}
            </span>
          </span>
          <textarea
            className="settings-input"
            rows={3}
            maxLength={NOTE_LIMIT}
            value={draft.note}
            onChange={(e) => set("note", e.target.value)}
            style={{ resize: "vertical" }}
          />
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

        <label className="consent-row auth-field">
          <input
            id="signup-consent"
            type="checkbox"
            required
            checked={draft.consent}
            onChange={(e) => set("consent", e.target.checked)}
            aria-invalid={Boolean(errors.consent)}
            aria-describedby={errorId("consent")}
          />
          <span>
            I agree that Carlink may use these details to review my request and contact me about it.{" "}
            <Link href="/privacy" target="_blank" rel="noopener" className="setup-help-link">
              Privacy notice
            </Link>
          </span>
        </label>
        {fieldError("consent")}

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
          {busy ? "Sending…" : "Send request"}
        </button>

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
