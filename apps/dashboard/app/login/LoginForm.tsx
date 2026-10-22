"use client";

import { useState } from "react";
import Link from "next/link";
import { Eye, EyeOff } from "lucide-react";
import { API_BASE_URL } from "@/lib/api";
import { LoginBrand } from "@/components/LoginBrand";

export function LoginForm({ next }: { next: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // Straight from the browser so the API's Set-Cookie lands here.
      const res = await fetch(`${API_BASE_URL}/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const body = await res.json().catch(() => null);
      if (!res.ok) {
        setError(typeof body?.detail === "string" ? body.detail : `Sign-in failed (${res.status})`);
        setBusy(false);
        return;
      }
      // A full page load, so the layout picks up who is now signed in.
      window.location.assign(body?.must_change_password ? "/account" : next);
    } catch {
      setError("Couldn't reach the server. Check your connection and try again.");
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <form className="login-card card-glass" onSubmit={submit}>
        <LoginBrand />

        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Sign in</h1>
        <p className="setup-note" style={{ marginBottom: 18 }}>Use the email and password your admin set up for you.</p>

        <label style={{ display: "block", marginBottom: 14 }}>
          <span className="detail-field-label">Email</span>
          <input
            className="settings-input"
            type="email"
            autoComplete="username"
            autoFocus
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>

        <label style={{ display: "block", marginBottom: 16 }}>
          <span className="detail-field-label">Password</span>
          <div style={{ position: "relative" }}>
            <input
              className="settings-input"
              style={{ paddingRight: 38 }}
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
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

        {error && (
          <div className="setup-error" role="alert">
            {error}
          </div>
        )}

        <button type="submit" className="btn-primary-modern" style={{ width: "100%", justifyContent: "center" }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p className="setup-note" style={{ margin: "16px 0 0", textAlign: "center" }}>
          Forgot your password? Ask your admin to reset it.
        </p>

        <div className="login-divider" />
        <p className="setup-note" style={{ margin: 0, textAlign: "center" }}>
          New to Carlink Studio?{" "}
          <Link href="/signup" className="setup-help-link">
            Request access →
          </Link>
        </p>
      </form>
    </div>
  );
}
