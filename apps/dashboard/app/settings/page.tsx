import { settingsUser } from "./gate";
import { PasswordCard, ProfileCard } from "./MyAccountCards";
import { SetupChecklist, profileSetupItems } from "./SetupChecklist";

/** Settings -> My settings: everyone's own account. Only GET /auth/me here,
 * nothing an admin-only call would refuse. */
export default async function MySettingsPage() {
  const me = await settingsUser();

  return (
    <>
      {/* 0. Setup checklist, only while something is missing. */}
      <SetupChecklist items={profileSetupItems(me)} />

      {/* 1. My profile (id="my-profile"). */}
      <ProfileCard user={me} />

      {/* 2. ---- SLOT: My AI key (P3-UI) ----
          <MyAiKeyCard initial={...} />, a SetupCard with id="my-ai-key"
          (other pages link to /settings#my-ai-key). Load its GET
          /auth/me/ai-key above, next to settingsUser(). */}

      {/* 3. ---- SLOT: My Telegram (P5) ----
          <MyTelegramCard initial={...} />, a SetupCard with id="my-telegram"
          (wording elsewhere: "Settings -> My Telegram"). */}

      {/* 4. Password (id="my-password"). */}
      <PasswordCard forced={false} />
    </>
  );
}
