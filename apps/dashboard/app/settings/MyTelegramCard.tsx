"use client";

import { useEffect, useRef, useState } from "react";
import { unstable_rethrow } from "next/navigation";
import { Check, Send, ShieldAlert, Unlink, X } from "lucide-react";
import type { MyTelegram } from "@/lib/api";
import {
  cancelTelegramCodeAction,
  confirmTelegramAction,
  createTelegramCodeAction,
  disconnectMyTelegramAction,
  getMyTelegramAction,
  type TelegramError,
  type TelegramResult,
} from "./actions";
import { SetupCard } from "./SetupCard";
import { TelegramWho } from "./TelegramWho";
import { apiTime, botHandle, botUrl, countdown, safeDeepLink, sameMoment, sgtDate, telegramPlain } from "./telegramWords";

/** Settings -> My settings -> My Telegram (id="my-telegram"): connect your
 * own Telegram, so the Carlink bot files the cases you send it under your
 * account (release P5).
 *
 * Four states, from GET /auth/me/telegram:
 *   - not connected: [Connect Telegram] makes a one-time code;
 *   - code open: the code, a countdown, [Open Telegram] (when the bot's
 *     @username is known) and [Cancel];
 *   - waiting: someone tapped "Yes, that's me" in Telegram with the code,
 *     and it waits for [Yes, connect] / [Not me] here (both sides confirm,
 *     security report H2);
 *   - connected: who, since when, and [Disconnect].
 *
 * While a code is open (and only then) the card asks again every 3 seconds,
 * so "waiting" shows up without a reload. It stops when the code is
 * answered, cancelled or expired, when the card goes away, and skips a turn
 * while the tab is hidden. */

const POLL_MS = 3000;

type Ok = Extract<TelegramResult, { telegram: MyTelegram }>;
type Busy = "code" | "cancel" | "yes" | "no" | "disconnect" | null;
type Notice = { tone: "ok" | "fail"; text: string } | null;

/** The plain code and its deep link, from POST .../code. GET doesn't send
 * them again (the API keeps only the code's hash), so they're kept here for
 * as long as that same code is open. */
type Shown = { display: string; deepLink: string | null; expiresAt: string };

/** Runs one of the actions. No answer at all becomes an error like any
 * other, so no button stays on "…ing". Next's redirects (signed out, a
 * temporary password) go through. */
async function settle(run: () => Promise<TelegramResult>): Promise<TelegramResult> {
  try {
    return await run();
  } catch (err) {
    unstable_rethrow(err);
    return { error: "Couldn't reach Carlink just now. Check your connection and try again.", code: null, status: null };
  }
}

/** The API's own words when it gave some, else plain ones for the two
 * refusals this card expects. */
function refusalText(result: TelegramError, fallback: string): string {
  const own = result.error && !/\(\d{3}\)$/.test(result.error) ? result.error : null;
  if (result.status === 429) return own || "You've made 10 codes in the last hour. Try again later.";
  if (result.status === 409) return own || "That has expired or was already answered. Make a new code to try again.";
  return result.error || fallback;
}

/** The card itself then says what that means. */
const CONNECTED_TEXT = "Connected.";

/** A code that was open or waiting went away without the account being
 * connected: it expired, or the API cancelled it because a second Telegram
 * answered it (security review M1). The card can't tell which. */
const CANCELLED_TEXT =
  "That code was cancelled: it expired, or another Telegram tried to use it. Make a new one if you still want to connect.";

export function MyTelegramCard({ initial }: { initial: MyTelegram | null }) {
  const [tg, setTg] = useState<MyTelegram | null>(initial);
  const [shown, setShown] = useState<Shown | null>(null);
  /** Server clock minus this device's, so the countdown follows the server. */
  const [skew, setSkew] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState<Busy>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);

  // Bumped by every button, so a poll already on its way when one was
  // pressed can't put back what it read before.
  const seq = useRef(0);
  // What the poll compares its answer with.
  const latest = useRef({ tg, shown });
  useEffect(() => {
    latest.current = { tg, shown };
  });

  const code = tg?.code ?? null;
  const expiresMs = code ? apiTime(code.expires_at) : NaN;
  const leftMs = expiresMs - (now + skew);
  // An expiry the API sent in a form that can't be read: keep it open and
  // let the API say when it ends.
  const live = Boolean(code) && !(leftMs <= 0);
  const pollKey = live && code ? code.expires_at : null;

  /** A fresh answer after something this card did. */
  const applied = (result: Ok) => {
    setSkew(result.now - Date.now());
    setNow(Date.now());
    setTg(result.telegram);
  };

  /** A fresh read (the poll, or asking again): what changed since the card
   * last looked, in words. A code that went away without connecting ends
   * up back at "not connected", saying so. */
  const polled = (result: Ok) => {
    const { tg: before, shown: had } = latest.current;
    const next = result.telegram;
    if (next.connected && !before?.connected) {
      setNotice({ tone: "ok", text: CONNECTED_TEXT });
    } else if (!next.code && before?.code && !next.connected) {
      setNotice({ tone: "fail", text: CANCELLED_TEXT });
    }
    // A different code (made in another tab): this one's plain code is no
    // use any more.
    if (had && !(next.code && sameMoment(next.code.expires_at, had.expiresAt))) setShown(null);
    applied(result);
  };

  // The countdown.
  useEffect(() => {
    if (!pollKey) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [pollKey]);

  // Every 3 seconds while a code is open; one request at a time.
  useEffect(() => {
    if (!pollKey) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      if (stopped) return;
      if (document.visibilityState !== "hidden") {
        const started = seq.current;
        const result = await settle(() => getMyTelegramAction());
        if (stopped) return;
        // A failed poll is just tried again next time.
        if (started === seq.current && "telegram" in result) polled(result);
      }
      if (!stopped) timer = window.setTimeout(poll, POLL_MS);
    };
    timer = window.setTimeout(poll, POLL_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
    // polled() reads only refs and state setters.
  }, [pollKey]);

  // Expired here: stop (pollKey goes) and say so.
  useEffect(() => {
    if (!code || live) return;
    setTg((t) => (t ? { ...t, code: null } : t));
    setShown(null);
    setNotice({ tone: "fail", text: CANCELLED_TEXT });
  }, [code, live]);

  /** Runs a button's action; polls started before or during it are dropped. */
  const run = async (kind: Exclude<Busy, null>, action: () => Promise<TelegramResult>) => {
    seq.current += 1;
    setBusy(kind);
    setNotice(null);
    const result = await settle(action);
    seq.current += 1;
    setBusy(null);
    return result;
  };

  /** Asks the API again; what it said, or null. Dropped if a button was
   * pressed meanwhile. */
  const reload = async (): Promise<MyTelegram | null> => {
    setReloading(true);
    const started = ++seq.current;
    const result = await settle(() => getMyTelegramAction());
    setReloading(false);
    if (started !== seq.current) return null;
    if ("error" in result) {
      setLoadError(result.error || "Couldn't load your Telegram just now.");
      return null;
    }
    setLoadError(null);
    polled(result);
    return result.telegram;
  };

  // The page is drawn again on the server after some changes elsewhere on
  // it: your own password change ends the Telegram link, and revalidates
  // the page. Its answer can reach this card late, though -- Next may apply
  // one revalidation only after a later action (a code made since) -- so
  // it isn't taken as it is: a new one only makes the card ask the API
  // again. Not while a button is busy; that button's answer is fresher.
  const initialKey = JSON.stringify(initial);
  const seenKey = useRef(initialKey);
  useEffect(() => {
    if (initialKey === seenKey.current) return;
    seenKey.current = initialKey;
    if (!busy) void reload();
    // Only a new page answer should trigger this.
  }, [initialKey]);

  const makeCode = async () => {
    const result = await run("code", () => createTelegramCodeAction());
    if ("error" in result) {
      setNotice({ tone: "fail", text: refusalText(result, "Couldn't make a code. Try again.") });
      return;
    }
    applied(result);
    const c = result.telegram.code;
    setShown(c?.display ? { display: c.display, deepLink: c.deep_link, expiresAt: c.expires_at } : null);
  };

  const cancelCode = async () => {
    const result = await run("cancel", () => cancelTelegramCodeAction());
    if ("error" in result) {
      if (result.code === "done") {
        setTg((t) => (t ? { ...t, code: null } : t));
        setShown(null);
        return;
      }
      setNotice({ tone: "fail", text: refusalText(result, "Couldn't cancel the code. Try again.") });
      return;
    }
    setShown(null);
    applied(result);
  };

  /** [Yes, connect] or [Not me]. */
  const answer = async (accept: boolean) => {
    const result = await run(accept ? "yes" : "no", () => confirmTelegramAction(accept));
    if ("error" in result) {
      const text = refusalText(result, accept ? "Couldn't connect. Try again." : "Couldn't cancel. Try again.");
      if (result.status === 409) {
        // Nothing waiting any more: show the card as it now is, then why.
        // The code gone (expired, or cancelled because another Telegram
        // answered it) is said the same way as when a poll finds it gone;
        // a Telegram just taken by another account in the API's words.
        const now = await reload();
        const gone = result.code !== "telegram_in_use" && !now?.code;
        setNotice({ tone: "fail", text: gone ? CANCELLED_TEXT : text });
        return;
      }
      setNotice({ tone: "fail", text });
      return;
    }
    setShown(null);
    applied(result);
    if (!accept) {
      setNotice({ tone: "ok", text: "Cancelled. That Telegram wasn't connected, and the code no longer works." });
    } else if (result.telegram.connected) {
      setNotice({ tone: "ok", text: CONNECTED_TEXT });
    } else {
      setNotice({ tone: "fail", text: "That didn't connect. Make a new code to try again." });
    }
  };

  const disconnect = async () => {
    if (!tg) return;
    const who = telegramPlain({ username: tg.telegram_username, name: tg.telegram_name });
    if (!confirm(`Disconnect your Telegram (${who})? The bot stops filing cases for you until you connect again.`)) return;
    const result = await run("disconnect", () => disconnectMyTelegramAction());
    if ("error" in result) {
      if (result.code !== "done") {
        setNotice({ tone: "fail", text: refusalText(result, "Couldn't disconnect. Try again.") });
        return;
      }
      setTg((t) => (t ? { ...t, connected: false, telegram_username: null, telegram_name: null, linked_at: null } : t));
    } else {
      applied(result);
    }
    setNotice({ tone: "ok", text: "Disconnected. The bot no longer files cases for you." });
  };

  const icon = <Send style={{ width: 22, height: 22, color: "var(--accent-cyan)" }} />;
  const title = "My Telegram";
  const subtitle = "File cases from your phone by sending photos to the Carlink bot";

  if (!tg) {
    return (
      <SetupCard id="my-telegram" icon={icon} title={title} subtitle={subtitle} status={{ ok: false, label: "Couldn't load" }}>
        <p className="setup-note">{loadError || "Couldn't load your Telegram just now."}</p>
        <button type="button" className="btn-secondary-modern" onClick={() => void reload()} disabled={reloading}>
          {reloading ? "Loading…" : "Try again"}
        </button>
      </SetupCard>
    );
  }

  const bot = botHandle(tg.bot_username);
  const botName = bot ?? "the Carlink bot";
  const waiting = live && code?.waiting ? code.waiting : null;
  const phase = waiting ? "waiting" : live && code ? "code" : tg.connected ? "connected" : "none";

  // The plain code: in this answer (straight after POST), or kept from it.
  const plain = code && shown && sameMoment(shown.expiresAt, code.expires_at) ? shown : null;
  const display = code?.display || plain?.display || null;
  const deepLink = safeDeepLink(code?.deep_link || plain?.deepLink);
  const timeLeft = isNaN(leftMs) ? null : countdown(leftMs);

  const status =
    phase === "connected"
      ? { ok: true, label: "Connected" }
      : phase === "waiting"
        ? { ok: false, label: "Confirm below" }
        : phase === "code"
          ? { ok: false, label: "Waiting for Telegram" }
          : { ok: false, label: "Not connected" };

  const anyBusy = busy !== null;

  return (
    <SetupCard
      id="my-telegram"
      icon={icon}
      title={title}
      subtitle={subtitle}
      status={status}
      helpUrl={phase === "connected" ? botUrl(tg.bot_username) ?? undefined : undefined}
      helpLabel={bot ? `Open ${bot} in Telegram` : undefined}
    >
      {notice && (
        <div className={notice.tone === "ok" ? "settings-notice" : "setup-error"} role="status">
          {notice.tone === "ok" && <Check style={{ width: 14, height: 14, flexShrink: 0, marginTop: 1 }} />}
          <span style={{ flex: 1, minWidth: 0 }}>{notice.text}</span>
          {notice.tone === "ok" && (
            <button type="button" className="settings-notice-close" aria-label="Dismiss" onClick={() => setNotice(null)}>
              <X style={{ width: 13, height: 13 }} />
            </button>
          )}
        </div>
      )}

      {phase === "none" && (
        <>
          <p className="setup-note">
            Send case photos to {botName} on Telegram, and the cases are filed under your account. Connect your Telegram
            first.
          </p>
          <div className="tg-actions">
            <button type="button" className="btn-primary-modern" onClick={() => void makeCode()} disabled={anyBusy}>
              <Send style={{ width: 14, height: 14 }} /> {busy === "code" ? "Making a code…" : "Connect Telegram"}
            </button>
          </div>
          <p className="setup-note" style={{ margin: "12px 0 0" }}>
            You get a code to send to the bot. It works once, for 10 minutes.
            {!bot && " Ask your admin which bot to send it to."}
          </p>
        </>
      )}

      {phase === "code" && (
        <>
          <div className="tg-code-box">
            {display ? (
              <>
                <div className="tg-code-label">Your code</div>
                <div className="tg-code">{display}</div>
              </>
            ) : (
              <p className="setup-note" style={{ margin: 0 }}>
                A code is already open (made earlier, or in another tab). It is shown only once, so make a new one to see it.
              </p>
            )}
            {timeLeft && (
              <div className="tg-code-expiry" suppressHydrationWarning>
                Expires in {timeLeft}
              </div>
            )}
          </div>

          {display && (
            <ol className="tg-steps">
              <li>
                {deepLink ? (
                  <>
                    Tap <strong>Open Telegram</strong>, then <strong>Start</strong>. Or send the code to {botName}.
                  </>
                ) : (
                  <>
                    In Telegram, send {botName} this: <code className="tg-inline-code">/link {display}</code>
                  </>
                )}
              </li>
              <li>
                In Telegram, tap <strong>Yes, that&apos;s me</strong>.
              </li>
              <li>
                Come back here and tap <strong>Yes, connect</strong>.
              </li>
            </ol>
          )}

          <p className="tg-warning">
            <ShieldAlert style={{ width: 14, height: 14, flexShrink: 0, marginTop: 1 }} />
            <span>Only enter this code in your own Telegram. If someone asks you for it, don&apos;t share it.</span>
          </p>

          <div className="tg-actions">
            <button type="button" className="btn-secondary-modern" onClick={() => void cancelCode()} disabled={anyBusy}>
              {busy === "cancel" ? "Cancelling…" : "Cancel"}
            </button>
            {!display && (
              <button type="button" className="btn-primary-modern" onClick={() => void makeCode()} disabled={anyBusy}>
                {busy === "code" ? "Making a code…" : "Make a new code"}
              </button>
            )}
            {display && deepLink && (
              <a href={deepLink} target="_blank" rel="noopener noreferrer" className="btn-primary-modern tg-open-link">
                <Send style={{ width: 14, height: 14 }} /> Open Telegram
              </a>
            )}
          </div>
        </>
      )}

      {phase === "waiting" && waiting && (
        <div className="tg-waiting" role="alert">
          <div className="tg-waiting-title">A Telegram account wants to connect to your account.</div>
          <TelegramWho t={waiting} variant="rows" warnMissing />
          <p className="tg-waiting-check">
            {"Only tap Yes if this is the Telegram you just used. If you're not sure, tap Not me and make a new code."}
          </p>
          {timeLeft && (
            <p className="setup-note" style={{ margin: "4px 0 0" }} suppressHydrationWarning>
              {`Expires in ${timeLeft}.`}
            </p>
          )}
          <div className="tg-actions">
            <button type="button" className="btn-secondary-modern" onClick={() => void answer(false)} disabled={anyBusy}>
              {busy === "no" ? "Cancelling…" : "Not me"}
            </button>
            <button type="button" className="btn-primary-modern" onClick={() => void answer(true)} disabled={anyBusy}>
              <Check style={{ width: 14, height: 14 }} /> {busy === "yes" ? "Connecting…" : "Yes, connect"}
            </button>
          </div>
        </div>
      )}

      {phase === "connected" && (
        <>
          <div className="setup-key-row tg-linked-row">
            <Send style={{ width: 15, height: 15, color: "var(--accent-cyan)", flexShrink: 0 }} />
            <span className="tg-linked-who">
              <TelegramWho t={{ username: tg.telegram_username, name: tg.telegram_name }} variant="inline" />
              {tg.linked_at && sgtDate(tg.linked_at) && <span className="tg-linked-since">{` · since ${sgtDate(tg.linked_at)}`}</span>}
            </span>
            <button
              type="button"
              className="btn-secondary-modern users-action danger tg-disconnect"
              onClick={() => void disconnect()}
              disabled={anyBusy}
            >
              <Unlink style={{ width: 12, height: 12 }} /> {busy === "disconnect" ? "Disconnecting…" : "Disconnect"}
            </button>
          </div>
          <p className="setup-note" style={{ margin: "12px 0 0" }}>
            Cases you send to {botName} are filed under your account. Changing your password disconnects Telegram, so
            connect it again afterwards.
          </p>
        </>
      )}
    </SetupCard>
  );
}
