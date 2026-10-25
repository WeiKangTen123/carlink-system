import type { Metadata } from "next";
import Link from "next/link";
import { LoginBrand } from "@/components/LoginBrand";

export const metadata: Metadata = {
  title: "Privacy notice // Carlink Studio",
};

/** What Carlink Studio keeps about people who have an account, for
 * Singapore's PDPA. Linked from /signup. Keep it in step with what the API
 * actually stores and deletes (models.User, AuthSession, AuditEntry and the
 * cleanup sweep, apps/bot-service). */
export default function PrivacyPage() {
  return (
    <div className="login-screen">
      <main className="login-card card-glass privacy-notice">
        <LoginBrand />
        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Privacy notice</h1>
        <p className="setup-note">How Carlink Studio uses the details you give it.</p>

        <h2>What we collect</h2>
        <ul>
          <li>
            When you create an account: your username, email and password (stored only in a scrambled form that
            can&apos;t be turned back into the password), and when you signed up.
          </li>
          <li>
            Anything you add on My account, such as your full name, licence number and firm, which are printed on
            reports you sign off.
          </li>
          <li>
            While you use it: a security log of your sign-ins (time, network address and browser) and of what you do
            on cases.
          </li>
        </ul>

        <h2>Why</h2>
        <ul>
          <li>To create and manage your account.</li>
          <li>To show who did what on each case.</li>
          <li>To keep case files secure.</li>
        </ul>

        <h2>Who sees it</h2>
        <p>Carlink admins.</p>

        <h2>How long we keep it</h2>
        <ul>
          <li>
            Accounts are disabled, not deleted, when someone leaves, so the history on each case stays accurate.
          </li>
          <li>Sign-in sessions are deleted once they end; old invite and reset links after 30 days.</li>
        </ul>

        <h2>Seeing or correcting your details</h2>
        <p>
          You can change your username, full name and surveyor details on My account. For anything else, ask your
          Carlink administrator: they can show you what&apos;s held about you and correct it.
        </p>

        <div className="login-divider" />
        <p className="setup-note" style={{ margin: 0, textAlign: "center" }}>
          <Link href="/signup" className="setup-help-link">
            Create an account
          </Link>
          {"  ·  "}
          <Link href="/login" className="setup-help-link">
            Sign in
          </Link>
        </p>
      </main>
    </div>
  );
}
