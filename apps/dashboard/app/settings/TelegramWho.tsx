import type { TelegramIdentity } from "@/lib/api";
import { telegramName, telegramUsername } from "./telegramWords";

/** A Telegram account's @username and its name, shown apart and labelled,
 * never merged into one label (security review M1). The @username is in a
 * fixed-width font, so look-alike letters (l and I, O and 0) are easier to
 * tell apart.
 *
 * rows: one line each, "Telegram account" and "Name on Telegram" (My
 *   Telegram, when someone wants to connect).
 * inline: "@ahhuat_sg · Name on Telegram: Tan Ah Huat" (a connected
 *   account, on My Telegram and in Edit user).
 *
 * `warnMissing` words a missing @username as a warning: "no @username
 * (check carefully)". */
export function TelegramWho({
  t,
  variant,
  warnMissing = false,
}: {
  t: TelegramIdentity;
  variant: "rows" | "inline";
  warnMissing?: boolean;
}) {
  const username = telegramUsername(t);
  const name = telegramName(t);
  const user = username ? (
    <span className="tg-who-username">{username}</span>
  ) : (
    <span className={warnMissing ? "tg-who-missing warn" : "tg-who-missing"}>
      {warnMissing ? "no @username (check carefully)" : "no @username"}
    </span>
  );
  const named = name ? <span className="tg-who-name">{name}</span> : <span className="tg-who-missing">none</span>;

  if (variant === "rows") {
    return (
      <dl className="tg-who-rows">
        <div>
          <dt>Telegram account</dt>
          <dd>{user}</dd>
        </div>
        <div>
          <dt>Name on Telegram</dt>
          <dd>{named}</dd>
        </div>
      </dl>
    );
  }
  return (
    <span className="tg-who-inline">
      {user}
      {name && (
        <>
          <span className="tg-who-sep">{" · "}</span>
          <span className="tg-who-key">{"Name on Telegram: "}</span>
          {named}
        </>
      )}
    </span>
  );
}
