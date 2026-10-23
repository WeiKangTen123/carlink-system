import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/api";
import { SignupForm } from "./SignupForm";

export const metadata: Metadata = {
  title: "Request access // Carlink Studio",
};

export default async function SignupPage() {
  // Signed in already: there's nothing to ask for.
  const user = await getCurrentUser();
  if (user) redirect(user.must_change_password ? "/account" : "/");
  return <SignupForm />;
}
