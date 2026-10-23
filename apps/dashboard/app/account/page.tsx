import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/api";
import { AccountClient } from "./AccountClient";

export default async function AccountPage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login?next=/account");
  return <AccountClient user={user} />;
}
