import { unstable_rethrow } from "next/navigation";
import { getMyAiKey, getMyTelegram, type MyAiKey, type MyTelegram } from "@/lib/api";
import { isAdminUser, settingsUser } from "./gate";
import { MyAiKeyCard } from "./MyAiKeyCard";
import { MyTelegramCard } from "./MyTelegramCard";
import { PasswordCard, ProfileCard } from "./MyAccountCards";
import { SetupChecklist, aiKeySetupItem, profileSetupItems, telegramSetupItem } from "./SetupChecklist";

/** The person's own AI key or Telegram, or null when it couldn't be
 * loaded: the card then offers to try again, and the checklist leaves it
 * out. */
async function orNull<T>(load: () => Promise<T>, what: string): Promise<T | null> {
  try {
    return await load();
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next.
    unstable_rethrow(err);
    console.warn(`Settings: couldn't load ${what}:`, err);
    return null;
  }
}

/** Settings -> My settings: everyone's own account. Only GET /auth/me,
 * GET /auth/me/ai-key and GET /auth/me/telegram here, nothing an admin-only
 * call would refuse. */
export default async function MySettingsPage() {
  const [me, aiKey, telegram] = await Promise.all([
    settingsUser(),
    orNull<MyAiKey>(getMyAiKey, "My AI key"),
    orNull<MyTelegram>(getMyTelegram, "My Telegram"),
  ]);
  const aiItem = aiKey ? aiKeySetupItem(aiKey) : null;
  const telegramItem = telegram ? telegramSetupItem(telegram) : null;

  return (
    <>
      {/* 0. Setup checklist, only while something is missing. */}
      <SetupChecklist
        items={[...profileSetupItems(me), ...(aiItem ? [aiItem] : []), ...(telegramItem ? [telegramItem] : [])]}
      />

      {/* 1. My profile (id="my-profile"). */}
      <ProfileCard user={me} />

      {/* 2. My AI key (id="my-ai-key"; other pages link to
          /settings#my-ai-key). */}
      <MyAiKeyCard initial={aiKey} isAdmin={isAdminUser(me)} />

      {/* 3. My Telegram (id="my-telegram"; the bot's "connect first" reply
          points to Settings -> My Telegram). */}
      <MyTelegramCard initial={telegram} />

      {/* 4. Password (id="my-password"). */}
      <PasswordCard forced={false} />
    </>
  );
}
