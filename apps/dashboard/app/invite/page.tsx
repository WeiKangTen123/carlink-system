import type { Metadata } from "next";
import { InviteForm } from "./InviteForm";

export const metadata: Metadata = {
  title: "Set your password // Carlink Studio",
};

/** Where invite and password-reset links land (/invite#<token>). Works
 * while signed in too: using a link signs in as that account instead.
 * Response headers for this page are in next.config.ts. */
export default function InvitePage() {
  return <InviteForm />;
}
