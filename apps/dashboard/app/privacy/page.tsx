import type { Metadata } from "next";
import Link from "next/link";
import { LoginBrand } from "@/components/LoginBrand";
import { getPublicContact } from "@/lib/api";

export const metadata: Metadata = {
  title: "Privacy notice // Carlink Studio",
};

/** What Carlink Studio keeps about people who have an account, and (#public-
 * reports, release P6) about members of the public who send a report through
 * the Telegram bot, for Singapore's PDPA. Linked from /signup and from the
 * bot's consent message. Keep it in step with what the API actually stores
 * and deletes (models.User, AuthSession, AuditEntry, PublicSubmission,
 * PublicBlock, the cleanup sweep and the backups, apps/bot-service). */
export default async function PrivacyPage() {
  // Set by an admin on Settings -> Company; read without signing in.
  const contact = await getPublicContact();

  return (
    <div className="login-screen">
      <main className="login-card card-glass privacy-notice">
        <LoginBrand />
        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Privacy notice</h1>
        <p className="setup-note">How Carlink Studio uses the details you give it.</p>
        <p className="setup-note" style={{ marginTop: 0 }}>
          Sent a report through our Telegram bot? See{" "}
          <a href="#public-reports" className="setup-help-link">
            Reports sent through Telegram
          </a>
          .
        </p>

        <h2>What we collect</h2>
        <ul>
          <li>
            When you create an account: your username, email and password (stored only in a scrambled form that
            can&apos;t be turned back into the password), and when you signed up.
          </li>
          <li>
            Anything you add in Settings, such as your full name, licence number and firm, which are printed on
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
          You can change your username, full name and surveyor details in Settings. For anything else, ask your
          Carlink administrator: they can show you what&apos;s held about you and correct it.
        </p>

        {/* Release P6. The Telegram bot links here at its consent step; keep
            it in step with the consent text (bot-service telegram_public.py)
            and with what app/intake/public.py keeps and deletes. */}
        <section id="public-reports" className="privacy-public-section">
          <div className="login-divider" />
          <h1 style={{ margin: "0 0 4px", fontSize: 18, fontWeight: 800 }}>Reports sent through Telegram</h1>
          <p className="setup-note">
            For members of the public who send us a car accident or damage report through our Telegram bot.
          </p>

          <h2>What we collect</h2>
          <ul>
            <li>Your name, and whether you are the car&apos;s driver, its owner, a workshop or someone else.</li>
            <li>Your phone number, shared from your Telegram account.</li>
            <li>The car plate, when and where it happened, what happened in your own words, and your photos.</li>
            <li>
              Your Telegram account&apos;s number (its ID), username and name, so the bot can reply to you and you can
              withdraw your report. We stop keeping these once your report becomes a case.
            </li>
          </ul>

          <h2>Why</h2>
          <p>
            Only to handle your report: to contact you, assess the damage, and deal with the insurer, workshop or others
            involved in the claim. Never for marketing.
          </p>

          <h2>Who sees it</h2>
          <ul>
            <li>Carlink staff, and the insurer, workshop or others involved in the claim.</li>
            <li>
              Our staff may use Google&apos;s Gemini AI to read your photos and what you write. Google may process this
              data outside Singapore.
            </li>
            <li>Your messages to the bot also pass through Telegram.</li>
          </ul>

          <h2>How long we keep it</h2>
          <ul>
            <li>
              Reports we haven&apos;t taken up within 30 days are deleted, with their photos. Reports we decide not to
              take up (for example duplicates) are deleted straight away.
            </li>
            <li>
              Once we take a report up and it becomes a case, it is kept like our other case files, with your name, phone
              number and what you wrote.
            </li>
            <li>
              Our database backups, which may still hold a deleted report&apos;s details, are kept for up to 30 days.
              Photos of reports we haven&apos;t taken up are not copied to backups.
            </li>
            <li>
              If we discard a report as spam, we keep that Telegram account&apos;s number for 90 days, so the bot can
              refuse more reports from it.
            </li>
          </ul>

          <h2>Withdrawing your report</h2>
          <p>
            Send /withdraw to the bot. A report we haven&apos;t taken up yet is deleted at once. If we have already taken
            it up, we are told you want it withdrawn, and we will contact you.
          </p>

          <h2>Please don&apos;t send</h2>
          <p>Photos of your NRIC. Only include other people&apos;s details if the report needs them.</p>

          <h2>Contact</h2>
          <p>
            {contact.dpo_contact ? (
              <>
                Data protection contact: <span className="privacy-contact">{contact.dpo_contact}</span>
              </>
            ) : (
              "Contact: ask Carlink for the data protection contact."
            )}
            {contact.public_contact_phone && (
              <>
                <br />
                About a report you sent: call <span className="privacy-contact">{contact.public_contact_phone}</span>
              </>
            )}
          </p>
        </section>

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
