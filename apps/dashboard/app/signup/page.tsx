import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getCurrentUser, getSignupsOpen } from "@/lib/api";
import { SignupForm } from "./SignupForm";

export const metadata: Metadata = {
  title: "Create an account // Carlink Studio",
};

export default async function SignupPage() {
  // Signed in already: there's nothing to sign up for.
  const [user, open] = await Promise.all([getCurrentUser(), getSignupsOpen()]);
  if (user) redirect(user.must_change_password ? "/account" : "/");
  return <SignupForm open={open} />;
}
