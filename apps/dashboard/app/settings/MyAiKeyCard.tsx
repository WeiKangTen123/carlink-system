"use client";

import { useState } from "react";
import { Check, Eye, EyeOff, Key, RefreshCw, Sparkles, Trash2, X, Zap } from "lucide-react";
import type { MyAiKey } from "@/lib/api";
import { formatSgt } from "@/lib/signup";
import { deleteMyAiKeyAction, getMyAiKeyAction, saveMyAiKeyAction, testMyAiKeyAction, type AiKeyResult } from "./actions";
import {
  AI_STUDIO_KEYS_URL,
  FAILING_FIX,
  FAILING_WHAT,
  aiKeyWord,
  isFailingStatus,
  keyFormatProblem,
  maskedKey,
} from "./aiKeyWords";
import { SetupCard, TestResult, type TestState } from "./SetupCard";

/** Settings -> My settings -> My AI key (id="my-ai-key"): the person's own
 * Gemini key. Everyone, admins included, sees only its last 4 characters
 * and a status, and the key typed here is never shown again: the field is
 * cleared once it's saved.
 *
 * Release P3 only stores the key. The AI still runs on the company key, for
 * admins and the people an admin allows; own keys are used from the next
 * update. So nothing here says "your AI works now" because of a key --
 * GET /auth/me/ai-key's `source` and `ai_available` are about that next
 * update and aren't shown. */

/** What the card shows after something happened. */
type Notice = { tone: "ok" | "fail"; text: string } | null;

/** After saving: what Google said, and what that means in this release. */
function savedText(key: MyAiKey): string {
  const google =
    key.status === "ok"
      ? "Google accepted it."
      : key.status === "untested"
        ? "Google couldn't be reached to check it; press Test later."
        : key.status === "quota"
          ? "Google accepted it, but it has no allowance left for now."
          : "";
  const when = key.company_allowed
    ? "Your key will be used for AI from the next update; until then AI uses the company key, as now."
    : "Your key will be used for AI from the next update; until then AI uses the company key if an admin allows you.";
  return ["Saved.", google, when].filter(Boolean).join(" ");
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
 * the card's state again didn't. */
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
  };
}

/** "AI right now": the truth for this release, whatever key is saved. */
function AiRightNow({ ai, isAdmin }: { ai: MyAiKey; isAdmin: boolean }) {
  let text: string;
  if (isAdmin) {
    text = "The AI runs on the company key for you: admins can always use it.";
  } else if (ai.company_allowed) {
    text = "The AI runs on the company key for you: an admin has allowed it.";
  } else {
    text =
      "The AI buttons are off for you for now: the AI runs on the company key, and an admin hasn't allowed you to use it yet. Admins can let you use the company key; ask one if you need it.";
  }
  return (
    <div className="ai-key-now">
      <Sparkles style={{ width: 14, height: 14, flexShrink: 0, marginTop: 2 }} />
      <span>
        <strong>AI right now.</strong> {text} {ai.has_key ? "Your own key takes over from the next update." : "Your own key will be used for AI from the next update."}
      </span>
    </div>
  );
}

/** The paste-a-key form, for a first key or a replacement. */
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
  const [billing, setBilling] = useState(false);
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
    if (!billing) {
      setError("Tick the box to confirm billing is turned on for this key's Google Cloud project.");
      return;
    }
    setSaving(true);
    setError(null);
    const result = await saveMyAiKeyAction(typed, true);
    setSaving(false);
    if ("error" in result) {
      setError(result.error || "Failed to save your AI key");
      return;
    }
    // Never kept on screen once it's saved.
    setValue("");
    setShow(false);
    setBilling(false);
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

      <label className="user-tick ai-key-billing">
        <input
          type="checkbox"
          checked={billing}
          disabled={saving}
          onChange={(e) => {
            setBilling(e.target.checked);
            setError(null);
          }}
        />
        <span>
          This key&apos;s Google Cloud project has billing turned on
          <span className="field-hint">
            Why it matters: on free keys, Google may use what Carlink sends it, case photos included, to improve its
            products, and people at Google may read it. With billing on, it doesn&apos;t.
          </span>
        </span>
      </label>

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
          disabled={saving || !value.trim() || !billing}
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <Key style={{ width: 14, height: 14 }} /> {saving ? "Checking with Google…" : replacing ? "Save new key" : "Save key"}
        </button>
      </div>
    </form>
  );
}

export function MyAiKeyCard({ initial, isAdmin }: { initial: MyAiKey | null; isAdmin: boolean }) {
  const [ai, setAi] = useState<MyAiKey | null>(initial);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [busy, setBusy] = useState<"test" | "remove" | null>(null);
  const [tested, setTested] = useState<TestState>(null);
  const [notice, setNotice] = useState<Notice>(null);

  const icon = <Key style={{ width: 22, height: 22, color: "var(--accent-primary)" }} />;
  const title = "My AI key";
  const subtitle = "Your own Gemini key, for the AI that reads case photos";

  const reload = async () => {
    setReloading(true);
    const result = await getMyAiKeyAction();
    setReloading(false);
    if ("error" in result) setLoadError(result.error || "Failed to load your AI key");
    else {
      setLoadError(null);
      setAi(result.key);
    }
  };

  if (!ai) {
    return (
      <SetupCard id="my-ai-key" icon={icon} title={title} subtitle={subtitle} status={{ ok: false, label: "Couldn't load" }}>
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

  /** A refusal from Test or Remove. no_ai_key: it's gone already (another
   * tab, or an admin), so show the card as it now is. */
  const refused = async (result: Extract<AiKeyResult, { error: string }>) => {
    setNotice({ tone: "fail", text: result.error || "Something went wrong. Try again." });
    if (result.code === "no_ai_key") await reload();
  };

  const runTest = async () => {
    setBusy("test");
    setTested(null);
    setNotice(null);
    const result = await testMyAiKeyAction();
    setBusy(null);
    if ("error" in result) return refused(result);
    setAi(result.key);
    setTested(testedResult(result.key));
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
    const result = await deleteMyAiKeyAction();
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
      helpUrl={AI_STUDIO_KEYS_URL}
      helpLabel="Get a Gemini API key at Google AI Studio"
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
              Own key <span className="ai-key-mask">{maskedKey(ai.last4)}</span>
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
        <>
          <ol className="ai-key-steps">
            <li>Open Google AI Studio&apos;s API keys page (link above) and sign in with your Google account.</li>
            <li>Create a key in a Google Cloud project that has billing turned on.</li>
            <li>Copy the key and paste it here.</li>
          </ol>
          <KeyForm replacing={false} onSaved={saved} />
        </>
      )}

      <p className="setup-note" style={{ margin: "12px 0 0" }}>
        Once saved, the key is locked away: nobody, admins included, can see it again. Carlink shows only its last 4
        characters. One key per person; a key someone else uses can&apos;t be added.
      </p>

      <AiRightNow ai={ai} isAdmin={isAdmin} />
    </SetupCard>
  );
}
