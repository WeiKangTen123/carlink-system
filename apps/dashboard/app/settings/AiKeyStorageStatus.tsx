import { ShieldAlert, ShieldCheck } from "lucide-react";

/** GET /system/info's ai_key_storage, narrowed to what's shown. */
export type AiKeyStorageView = { state: string; detail: string | null };

/** What each state means, in a few words. The API's `detail` says what
 * to do about it (copy the old secret file back, or run the reset
 * command), so it is shown as it comes. */
const STATE_WORDS: Record<string, string> = {
  lost: "The secret they were sealed with is lost.",
  broken: "The secret file that seals them can't be read.",
  canary_failed: "The secret file on this server doesn't match the saved keys.",
  off: "Storing them is off on this server.",
};

/** Settings -> Company: whether this server can read people's own AI keys
 * (My AI key). One quiet line when it can; a warning with the API's fix
 * when it can't. Nothing at all when the API didn't say (an older API, or
 * GET /system/info failed): no "stored safely" without knowing. */
export function AiKeyStorageStatus({ storage }: { storage: AiKeyStorageView | null }) {
  if (!storage) return null;

  if (storage.state === "ok") {
    return (
      <p className="ai-key-storage-ok">
        <ShieldCheck style={{ width: 14, height: 14, flexShrink: 0 }} />
        <span>People&apos;s own AI keys: stored safely, sealed on this server.</span>
      </p>
    );
  }

  return (
    <div className="setup-warning-banner ai-key-storage-warning" role="alert">
      <div className="ai-key-storage-head">
        <ShieldAlert style={{ width: 16, height: 16, flexShrink: 0 }} />
        <strong>People&apos;s own AI keys can&apos;t be read on this server</strong>
      </div>
      {STATE_WORDS[storage.state] && <p className="ai-key-storage-line">{STATE_WORDS[storage.state]}</p>}
      {storage.detail && <p className="ai-key-storage-detail">{storage.detail}</p>}
      <p className="ai-key-storage-line">
        Until it&apos;s fixed, their keys show as Failing on My AI key and on Users. The company AI key isn&apos;t
        affected. <span className="ai-key-storage-state">State: {storage.state}</span>
      </p>
    </div>
  );
}
