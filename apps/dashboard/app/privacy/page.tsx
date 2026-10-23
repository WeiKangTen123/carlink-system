import type { Metadata } from "next";
import Link from "next/link";
import { LoginBrand } from "@/components/LoginBrand";

export const metadata: Metadata = {
  title: "Privacy notice // Carlink Studio",
};

/** What Carlink Studio keeps about people who ask for access or have an
 * account, for Singapore's PDPA. Linked from the consent box on /signup.
 * Keep it in step with what the API actually stores and deletes
 * (AccessRequest and the cleanup sweep, apps/bot-service). */
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
            When you request access: your name, email, mobile number, company and any note you write, with the time
            you sent it and the network address and browser it came from (to spot abuse).
          </li>
          <li>When you have an account: a security log of your sign-ins and of what you do on cases.</li>
        </ul>

        <h2>Why</h2>
        <ul>
          <li>To review access requests.</li>
          <li>To create and manage accounts.</li>
          <li>To contact you about your access.</li>
          <li>To keep case files secure.</li>
        </ul>

        <h2>Who sees it</h2>
        <p>Carlink admins.</p>

        <h2>How long we keep it</h2>
        <ul>
          <li>Requests that are rejected or never answered are deleted after 30 to 60 days.</li>
          <li>
            Accounts are disabled, not deleted, when someone leaves, so the history on each case stays accurate.
          </li>
        </ul>

        <h2>Seeing or correcting your details</h2>
        <p>
          Ask your Carlink administrator. They can show you what&apos;s held about you, correct it, or delete a request
          you sent.
        </p>

        <div className="login-divider" />
        <p className="setup-note" style={{ margin: 0, textAlign: "center" }}>
          <Link href="/signup" className="setup-help-link">
            Request access
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
