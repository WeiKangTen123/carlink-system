import { unstable_rethrow } from "next/navigation";
import { getMyAiKey, type MyAiKey } from "@/lib/api";
import { isAdminUser, settingsUser } from "./gate";
import { MyAiKeyCard } from "./MyAiKeyCard";
import { PasswordCard, ProfileCard } from "./MyAccountCards";
import { SetupChecklist, aiKeySetupItem, profileSetupItems } from "./SetupChecklist";

/** The person's own AI key, or null when it couldn't be loaded: the card
 * then offers to try again, and the checklist leaves it out. */
async function myAiKeyOrNull(): Promise<MyAiKey | null> {
  try {
    return await getMyAiKey();
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next.
    unstable_rethrow(err);
    console.warn("Settings: couldn't load My AI key:", err);
    return null;
  }
}

/** Settings -> My settings: everyone's own account. Only GET /auth/me and
 * GET /auth/me/ai-key here, nothing an admin-only call would refuse. */
export default async function MySettingsPage() {
  const [me, aiKey] = await Promise.all([settingsUser(), myAiKeyOrNull()]);
  const aiItem = aiKey ? aiKeySetupItem(aiKey) : null;

  return (
    <>
      {/* 0. Setup checklist, only while something is missing. */}
      <SetupChecklist items={[...profileSetupItems(me), ...(aiItem ? [aiItem] : [])]} />

      {/* 1. My profile (id="my-profile"). */}
      <ProfileCard user={me} />

      {/* 2. My AI key (id="my-ai-key"; other pages link to
          /settings#my-ai-key). */}
      <MyAiKeyCard initial={aiKey} isAdmin={isAdminUser(me)} />

      {/* 3. ---- SLOT: My Telegram (P5) ----
          <MyTelegramCard initial={...} />, a SetupCard with id="my-telegram"
          (wording elsewhere: "Settings -> My Telegram"). */}

      {/* 4. Password (id="my-password"). */}
      <PasswordCard forced={false} />
    </>
  );
}
