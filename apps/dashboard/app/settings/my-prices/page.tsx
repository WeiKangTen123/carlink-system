import { unstable_rethrow } from "next/navigation";
import { listMyPrices, type MyPrice } from "@/lib/api";
import { settingsUser } from "../gate";
import { MyPricesClient } from "../MyPricesClient";

/** Settings -> My prices (Stage 1, owner 24 Nov 2026): the prices this
 * person typed on Annex A, remembered per unit for them alone and filled in
 * on their next case of the same make and model. Every signed-in account,
 * price book or not: they are the person's own numbers (GET /me/prices
 * answers with the caller's rows only). A failed load shows the error on the
 * tab with Try again, not an error page. */
export default async function MyPricesSettingsPage() {
  await settingsUser();

  let prices: MyPrice[] | null = null;
  let error: string | null = null;
  try {
    prices = await listMyPrices();
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next.
    unstable_rethrow(err);
    console.warn("Settings: couldn't load My prices:", err);
    error = err instanceof Error ? err.message : "Failed to load your prices";
  }

  return <MyPricesClient initial={prices} initialError={error} />;
}
