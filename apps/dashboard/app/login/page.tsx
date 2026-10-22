import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/api";
import { LoginForm } from "./LoginForm";

export const metadata: Metadata = {
  title: "Sign in // Carlink Studio",
};

/** Only same-site paths, so ?next= can't send anyone to another website. */
function safeNext(next: string | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\") || next.startsWith("/login")) {
    return "/";
  }
  return next;
}

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const next = safeNext((await searchParams).next);
  // Already signed in (e.g. the Back button): go straight on.
  const user = await getCurrentUser();
  if (user) redirect(user.must_change_password ? "/account" : next);
  return <LoginForm next={next} />;
}
