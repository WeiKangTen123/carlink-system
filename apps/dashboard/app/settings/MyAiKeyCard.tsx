"use client";

import { useState } from "react";
import { unstable_rethrow } from "next/navigation";
import { Check, ExternalLink, Eye, EyeOff, Key, RefreshCw, Sparkles, Trash2, X, Zap } from "lucide-react";
import type { MyAiKey } from "@/lib/api";
import { formatSgt } from "@/lib/signup";
import {
  deleteMyAiKeyAction,
  getMyAiKeyAction,
  saveMyAiKeyAction,
  setMyAiKeyBilledAction,
  testMyAiKeyAction,
  type AiKeyError,
  type AiKeyResult,
} from "./actions";
import {
  AI_STUDIO_KEYS_URL,
  CLOUD_CREDENTIALS_URL,
  FAILING_FIX,
  FAILING_WHAT,
  KEY_TYPE_WORDS,
  aiKeyWord,
  aiRightNowWords,
  isFailingStatus,
  keyFormatProblem,
  maskedKey,
} from "./aiKeyWords";
import { AiAllowancePanel } from "./AiAllowancePanel";
import { HelpLink, SetupCard, TestResult, type TestState } from "./SetupCard";

/** Settings -> My settings -> My AI key (id="my-ai-key"): the person's own
 * Gemini key. Everyone, admins included, sees only its last 4 characters
 * and a status, and the key typed here is never shown again: the field is
 * cleared once it's saved.
 *
 * Since release P4 the AI runs on the key of whoever presses the button:
 * this key when it works, else the company key for admins and the people an
 * admin allows, else no AI. "AI right now" says which, from GET
 * /auth/me/ai-key's `source` and `ai_available`.
 *
 * Every key is Free or Billed (`billing_confirmed`), picked when it is saved
 * (Free unless changed) and switched later without pasting it again (PATCH).
 * A free key from Google AI Studio is the quickest way for anyone to get AI,
 * so it is allowed, with Google's terms for unpaid use spelt out whenever
 * Free is picked; only a Billed key is used for reports sent by the public.
 * "Today" shows each key's daily allowance (AiAllowancePanel). */

/** Runs one of the AI key actions. If no answer comes back at all (the
 * connection dropped, the server failed outright), that becomes an error
 * like any other, so no button is left on "Saving…" or "Testing…". Next's
 * own redirects pass through: signed out goes to sign-in, a temporary
 * password to /account, and the page is leaving anyway. */
async function settle(run: () => Promise<AiKeyResult>): Promise<AiKeyResult> {
  try {
    return await run();
  } catch (err) {
    unstable_rethrow(err);
    return { error: "Couldn't reach Carlink just now. Check your connection and try again.", code: null, status: null };
  }
}

/** The account itself was refused (disabled meanwhile, say), not the key. */
function accountRefused(result: AiKeyError): boolean {
  return result.status === 401 || result.status === 403;
}

/** What the card shows after something happened. */
type Notice = { tone: "ok" | "fail"; text: string } | null;

/** After saving: what Google said, and which key the AI uses now. */
function savedText(key: MyAiKey): string {
  const google =
    key.status === "ok"
      ? "Google accepted it."
      : key.status === "untested"
        ? "Google couldn't be reached to check it; press Test later."
        : key.status === "quota"
          ? "Google accepted it, but it has no allowance left for now."
          : "";
  const now =
    key.source === "own"
      ? "AI uses it from now on."
      : key.source === "company"
        ? "Until it works, AI uses the company key."
        : "AI stays off for you until it works.";
  return [key.billing_confirmed ? "Saved as a billed key." : "Saved as a free key.", google, now].filter(Boolean).join(" ");
}

/** After Test: Google's answer, in a few words. */
function testedResult(key: MyAiKey): TestState {
  switch (key.status) {
    case "ok":
      return { ok: true, message: "Google accepted the key" };
    case "invalid":
      return { ok: false, message: "Google refused the key" };
    case "quota":
      return { ok: false, message: "No allowance left for now" };
    case "unreadable":
      return { ok: false, message: "Can't read the saved key" };
    default:
      return { ok: true, message: "Not checked yet" };
  }
}

/** The same answer, without a key. Used when removing worked but reading
 * the card's state again didn't. With no own key, the AI is the company
 * key's or nothing. */
function withoutKey(key: MyAiKey): MyAiKey {
  return {
    ...key,
    has_key: false,
    last4: null,
    status: null,
    billing_confirmed: false,
    added_at: null,
    last_checked_at: null,
    last_error: null,
    ai_available: key.company_allowed,
    source: key.company_allowed ? "company" : "none",
  };
}

/** "AI right now": which key the AI uses for this person, as `source` and
 * `ai_available` say (aiRightNowWords). Whoever presses an AI button pays,
 * on any case. */
function AiRightNow({ ai, isAdmin }: { ai: MyAiKey; isAdmin: boolean }) {
  const { off, which, text } = aiRightNowWords(ai, isAdmin);
  return (
    <div className={`ai-key-now${off ? " off" : ""}`} role="status">
      <Sparkles style={{ width: 14, height: 14, flexShrink: 0, marginTop: 2 }} />
      <span>
        <span className="ai-key-now-label">AI right now</span>
        <strong>{which}</strong> {text}
      </span>
    </div>
  );
}

/** Type: (o) Free ( ) Billed, and what the choice means under it. For a key
 * being added (the form's own state) and for the saved one (each change is
 * a PATCH). `name` keeps the two radio groups apart when both are shown. */
function KeyTypeChoice({
  name,
  billed,
  disabled,
  onChange,
}: {
  name: string;
  billed: boolean;
  disabled: boolean;
  onChange: (billed: boolean) => void;
}) {
  return (
    <div className="ai-key-type">
      <div className="ai-key-type-row">
        <span className="detail-field-label" id={`${name}-label`}>
          Type
        </span>
        <div className="settings-choices ai-key-type-choices" role="radiogroup" aria-labelledby={`${name}-label`}>
          {[false, true].map((choice) => (
            <label key={String(choice)} className={`settings-choice${billed === choice ? " picked" : ""}`}>
              <input
                type="radio"
                name={name}
                checked={billed === choice}
                disabled={disabled}
                onChange={() => onChange(choice)}
              />
              <span className="settings-choice-title">{choice ? "Billed" : "Free"}</span>
            </label>
          ))}
        </div>
      </div>
      <span className="field-hint ai-key-type-words">{KEY_TYPE_WORDS[billed ? "billed" : "free"]}</span>
    </div>
  );
}

/** The paste-a-key form, for a first key or a replacement. Free unless
 * Billed is picked. */
function KeyForm({
  replacing,
  onSaved,
  onCancel,
}: {
  replacing: boolean;
  onSaved: (key: MyAiKey) => void;
  onCancel?: () => void;
}) {
  const [value, setValue] = useState("");
  const [show, setShow] = useState(false);
  const [billed, setBilled] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    const typed = value.trim();
    // Caught here so a typo doesn't use up one of the hour's saves.
    const problem = keyFormatProblem(typed);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    const result = await settle(() => saveMyAiKeyAction(typed, billed));
    setSaving(false);
    if ("error" in result) {
      setError(result.error || "Failed to save your AI key");
      // Not something a corrected key would fix: don't keep it on screen.
      if (accountRefused(result)) {
        setValue("");
        setShow(false);
        setBilled(false);
      }
      return;
    }
    // Never kept on screen once it's saved.
    setValue("");
    setShow(false);
    setBilled(false);
    onSaved(result.key);
  };

  return (
    <form className="ai-key-form" onSubmit={save} autoComplete="off">
      <label style={{ display: "block" }}>
        <span className="detail-field-label">{replacing ? "New Gemini API key" : "Gemini API key"}</span>
        <div style={{ position: "relative" }}>
          <input
            type={show ? "text" : "password"}
            name="gemini-api-key"
            className="settings-input"
            style={{ paddingRight: 38 }}
            placeholder="Paste the key (usually starts with AIza)"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setError(null);
            }}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            data-1p-ignore
            data-lpignore="true"
            disabled={saving}
          />
          <button
            type="button"
            className="setup-reveal-btn"
            onClick={() => setShow(!show)}
            aria-label={show ? "Hide the key" : "Show what you've pasted"}
            title={show ? "Hide" : "Show"}
            style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)" }}
          >
            {show ? <EyeOff style={{ width: 14, height: 14 }} /> : <Eye style={{ width: 14, height: 14 }} />}
          </button>
        </div>
      </label>

      <KeyTypeChoice
        name={replacing ? "ai-key-type-replace" : "ai-key-type-new"}
        billed={billed}
        disabled={saving}
        onChange={(b) => {
          setBilled(b);
          setError(null);
        }}
      />

      {error && (
        <div className="setup-error" role="alert" style={{ margin: 0 }}>
          {error}
        </div>
      )}

      <div className="ai-key-form-actions">
        {onCancel && (
          <button type="button" className="btn-secondary-modern" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
        )}
        <button
          type="submit"
          className="btn-primary-modern"
          disabled={saving || !value.trim()}
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <Key style={{ width: 14, height: 14 }} /> {saving ? "Checking with Google…" : replacing ? "Save new key" : "Save key"}
        </button>
      </div>
    </form>
  );
}

/** The three steps to a free key of one's own. One project per person:
 * Google's free allowance is per project, so two people sharing one run
 * out twice as fast. */
function HowToGetAKey() {
  return (
    <div className="ai-key-howto">
      <div className="detail-field-label">How to get a free key</div>
      <ol className="ai-key-steps">
        <li>
          Open{" "}
          <a href={AI_STUDIO_KEYS_URL} target="_blank" rel="noopener noreferrer" className="setup-help-link">
            Google AI Studio
          </a>{" "}
          and sign in with <strong>your own</strong> Google account.
        </li>
        <li>
          Press Create API key and create it in a new project. Two people in one project share one allowance.
        </li>
        <li>Copy the key, paste it above and pick Free.</li>
      </ol>
    </div>
  );
}

export function MyAiKeyCard({ initial, isAdmin }: { initial: MyAiKey | null; isAdmin: boolean }) {
  const [ai, setAi] = useState<MyAiKey | null>(initial);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [busy, setBusy] = useState<"test" | "remove" | "type" | null>(null);
  const [tested, setTested] = useState<TestState>(null);
  const [notice, setNotice] = useState<Notice>(null);

  const icon = <Key style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />;
  const title = "My AI key";
  const subtitle = "Your own Gemini key, for the AI that reads case photos";
  const getKey = (
    <a
      href={AI_STUDIO_KEYS_URL}
      target="_blank"
      rel="noopener noreferrer"
      className="btn-secondary-modern"
      title="Google AI Studio's API keys page: a free key takes a minute"
      style={{ fontSize: 11, padding: "5px 12px", display: "inline-flex", alignItems: "center", gap: 4, textDecoration: "none" }}
    >
      Get a key <ExternalLink style={{ width: 12, height: 12 }} />
    </a>
  );

  const reload = async () => {
    setReloading(true);
    const result = await settle(() => getMyAiKeyAction());
    setReloading(false);
    if ("error" in result) setLoadError(result.error || "Failed to load your AI key");
    else {
      setLoadError(null);
      setAi(result.key);
    }
  };

  if (!ai) {
    return (
      <SetupCard
        id="my-ai-key"
        icon={icon}
        title={title}
        subtitle={subtitle}
        headAction={getKey}
        status={{ ok: false, label: "Couldn't load" }}
      >
        <p className="setup-note">{loadError || "Couldn't load your AI key just now."}</p>
        <button type="button" className="btn-secondary-modern" onClick={() => void reload()} disabled={reloading}>
          {reloading ? "Loading…" : "Try again"}
        </button>
      </SetupCard>
    );
  }

  const word = aiKeyWord(ai.has_key, ai.status);
  const failing = ai.has_key && isFailingStatus(ai.status) ? ai.status : null;

  const saved = (key: MyAiKey) => {
    setAi(key);
    setReplacing(false);
    setTested(null);
    setNotice({ tone: "ok", text: savedText(key) });
  };

  /** A refusal from Test, Remove or the Free/Billed switch. no_ai_key: it's
   * gone already (another tab, or an admin); ai_key_changed: another tab
   * saved a new one. Either way, show the card as it now is. */
  const refused = async (result: AiKeyError) => {
    setNotice({ tone: "fail", text: result.error || "Something went wrong. Try again." });
    if (result.code === "no_ai_key" || result.code === "ai_key_changed") await reload();
  };

  const runTest = async () => {
    setBusy("test");
    setTested(null);
    setNotice(null);
    const result = await settle(() => testMyAiKeyAction());
    setBusy(null);
    if ("error" in result) return refused(result);
    setAi(result.key);
    setTested(testedResult(result.key));
  };

  const switchType = async (billed: boolean) => {
    if (billed === ai.billing_confirmed) return;
    setBusy("type");
    setTested(null);
    setNotice(null);
    const result = await settle(() => setMyAiKeyBilledAction(billed, ai.last4));
    setBusy(null);
    if ("error" in result) return refused(result);
    setAi(result.key);
    setNotice({
      tone: "ok",
      text: result.key.billing_confirmed
        ? "Your key is now Billed. It can also be used for reports sent by the public."
        : "Your key is now Free. It won't be used for reports sent by the public.",
    });
  };

  const remove = async () => {
    if (
      !confirm(
        "Remove your AI key from Carlink? It's deleted at once. The key itself still works at Google; delete it in Google AI Studio too if you no longer want it."
      )
    )
      return;
    setBusy("remove");
    setTested(null);
    setNotice(null);
    const result = await settle(() => deleteMyAiKeyAction());
    setBusy(null);
    if ("error" in result) {
      if (result.code === "removed") {
        setAi(withoutKey(ai));
        setReplacing(false);
        setNotice({ tone: "ok", text: "Your AI key is removed." });
        return;
      }
      return refused(result);
    }
    setAi(result.key);
    setReplacing(false);
    setNotice({ tone: "ok", text: "Your AI key is removed." });
  };

  return (
    <SetupCard
      id="my-ai-key"
      icon={icon}
      title={title}
      subtitle={subtitle}
      headAction={getKey}
      status={{ ok: word === "Working", label: word, fail: word === "Failing" }}
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

      {ai.has_key ? (
        <>
          <div className="setup-key-row ai-key-row">
            <Key style={{ width: 15, height: 15, color: "var(--accent-cyan)", flexShrink: 0 }} />
            <span className="ai-key-own">
              Key <span className="ai-key-mask">{maskedKey(ai.last4)}</span>
            </span>
            <span className={`ai-key-word ${failing ? "fail" : "ok"}`}>
              {word}
              {ai.status === "untested" && <span className="ai-key-word-sub"> · not checked yet</span>}
            </span>
            <span className="ai-key-row-actions">
              <TestResult result={tested} />
              <button
                type="button"
                className="btn-secondary-modern users-action"
                onClick={() => void runTest()}
                disabled={busy !== null}
                title="Ask Google about the key again (free: it uses none of the key's allowance)"
              >
                <Zap style={{ width: 12, height: 12 }} /> {busy === "test" ? "Testing…" : "Test"}
              </button>
              <button
                type="button"
                className="btn-secondary-modern users-action"
                onClick={() => {
                  setReplacing(!replacing);
                  setNotice(null);
                  setTested(null);
                }}
                disabled={busy !== null}
                aria-expanded={replacing}
              >
                <RefreshCw style={{ width: 12, height: 12 }} /> Replace
              </button>
              <button
                type="button"
                className="btn-secondary-modern users-action danger"
                onClick={() => void remove()}
                disabled={busy !== null}
              >
                <Trash2 style={{ width: 12, height: 12 }} /> {busy === "remove" ? "Removing…" : "Remove"}
              </button>
            </span>
          </div>
          <div className="ai-key-dates">
            {ai.added_at && <span>Added {formatSgt(ai.added_at)}</span>}
            <span>{ai.last_checked_at ? `Last checked ${formatSgt(ai.last_checked_at)}` : "Not checked with Google yet"}</span>
          </div>

          <KeyTypeChoice
            name="ai-key-type-saved"
            billed={ai.billing_confirmed}
            disabled={busy !== null}
            onChange={(b) => void switchType(b)}
          />

          {failing && (
            <div className="setup-error ai-key-failing" role="alert">
              <div>{ai.last_error || FAILING_WHAT[failing]}</div>
              <div className="ai-key-failing-fix">{FAILING_FIX[failing]}</div>
            </div>
          )}

          {replacing && (
            <div className="ai-key-replace">
              <KeyForm replacing onSaved={saved} onCancel={() => setReplacing(false)} />
            </div>
          )}
        </>
      ) : (
        <KeyForm replacing={false} onSaved={saved} />
      )}

      <AiAllowancePanel refreshOn={`${ai.has_key}|${ai.last4}|${ai.status}|${ai.billing_confirmed}|${ai.source}`} />

      {(!ai.has_key || replacing) && <HowToGetAKey />}

      <p className="setup-note" style={{ margin: "12px 0 0" }}>
        Once saved, the key is locked away: nobody, admins included, can see it again. Carlink shows only its last 4
        characters. One key per person; a key someone else uses can&apos;t be added.
      </p>
      <p className="setup-note ai-key-tip">
        <strong>Tip:</strong> in Google Cloud, restrict the key to the Generative Language API, and if you like to
        this server&apos;s address (ask your admin).{" "}
        <HelpLink url={CLOUD_CREDENTIALS_URL} label="Key settings in Google Cloud" />
      </p>

      <AiRightNow ai={ai} isAdmin={isAdmin} />
    </SetupCard>
  );
}
