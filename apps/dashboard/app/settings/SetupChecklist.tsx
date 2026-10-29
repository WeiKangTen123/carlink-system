import { CheckCircle2, Circle, ListChecks } from "lucide-react";
import { aiKeyWorking, type CurrentUser, type MyAiKey } from "@/lib/api";

/** One thing to set up on My settings. `href` is the card it's done on;
 * `action` the link's words (default "Add it"). */
export type SetupItem = { key: string; label: string; hint: string; done: boolean; href: string; action?: string };

/** What My settings asks everyone to set up, from what GET /auth/me says.
 *
 * Later releases add their own items here, reading the same state their
 * card shows ("Working" / "Not set up" / "Failing"):
 *   - "AI key" (P3): aiKeySetupItem below.
 *   - SLOT (P5): "Telegram" -> #my-telegram, done once connected. */
export function profileSetupItems(me: CurrentUser): SetupItem[] {
  const items: SetupItem[] = [
    {
      key: "name",
      label: "Add your full name",
      hint: "Shown on your cases and printed on reports you sign off. Until then Carlink uses your username.",
      done: Boolean((me.name || "").trim()) && me.name !== me.username,
      href: "#my-profile",
    },
  ];
  // Only printed on reports you sign off, so only asked of accounts that may.
  if (me.may_sign_off) {
    items.push({
      key: "licence",
      label: "Add your licence number",
      hint: "Printed beside your name on reports you sign off.",
      done: Boolean((me.license_number || "").trim()),
      href: "#my-profile",
    });
  }
  return items;
}

/** "AI key" -> #my-ai-key. Done when the person's own key is working (or
 * not checked yet), or when they may use the company key anyway (admins,
 * and accounts an admin allowed). A failing key asks to be fixed. */
export function aiKeySetupItem(ai: MyAiKey): SetupItem {
  const ownWorking = ai.has_key && aiKeyWorking(ai.status);
  const done = ownWorking || ai.company_allowed;
  if (!done && ai.has_key) {
    return {
      key: "ai-key",
      label: "Fix your AI key",
      hint:
        ai.status === "quota"
          ? "Your key has used up its allowance for now, so AI is off for you. It should work again within about an hour; try again later."
          : "The key you saved isn't working, so AI is off for you. Replace it on My AI key.",
      done,
      href: "#my-ai-key",
      action: "Fix it",
    };
  }
  return {
    key: "ai-key",
    label: "Set up AI",
    hint: "Add your own Gemini key on My AI key, so the AI runs on it, or ask an admin to let you use the company key.",
    done,
    href: "#my-ai-key",
  };
}

/** The top of My settings, shown only while something is still missing.
 * Done items stay ticked in the list so it reads as progress. */
export function SetupChecklist({ items }: { items: SetupItem[] }) {
  const missing = items.filter((i) => !i.done).length;
  if (missing === 0) return null;
  return (
    <div className="card-glass setup-card settings-checklist" role="region" aria-label="Finish setting up">
      <div className="setup-card-head" style={{ marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12, minWidth: 0 }}>
          <div className="setup-card-icon">
            <ListChecks style={{ width: 22, height: 22, color: "var(--badge-amber-text)" }} />
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="card-title" style={{ marginBottom: 2 }}>Finish setting up</div>
            <div className="card-subtitle" style={{ marginBottom: 0 }}>
              {missing === 1 ? "One thing left" : `${missing} things left`}. This goes away once it&apos;s all done.
            </div>
          </div>
        </div>
        <span className="setup-status">
          {items.length - missing} of {items.length} done
        </span>
      </div>
      <ul className="settings-checklist-list">
        {items.map((item) => (
          <li key={item.key} className={item.done ? "done" : undefined}>
            {item.done ? (
              <CheckCircle2 style={{ width: 16, height: 16, color: "var(--badge-green-text)", flexShrink: 0 }} />
            ) : (
              <Circle style={{ width: 16, height: 16, color: "var(--badge-amber-text)", flexShrink: 0 }} />
            )}
            <span style={{ flex: 1, minWidth: 0 }}>
              <span className="settings-checklist-label">{item.label}</span>
              {!item.done && <span className="settings-checklist-hint">{item.hint}</span>}
            </span>
            {!item.done && (
              <a href={item.href} className="setup-help-link">
                {item.action || "Add it"}
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
