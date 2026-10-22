import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/api";
import { isPublicPage } from "@/lib/publicPages";
import { LoginForm } from "./LoginForm";

export const metadata: Metadata = {
  title: "Sign in // Carlink Studio",
};

/** Only a path on this site, so ?next= can't send anyone to another
 * website. Checked by resolving it the way the browser will: URL parsing
 * drops tabs and newlines, so "/<TAB>/evil.com" would otherwise become
 * "//evil.com". Never a public page such as /login or /signup itself. */
function safeNext(next: string | undefined): string {
  if (!next || !next.startsWith("/") || /[\u0000-\u001f\u007f\\]/.test(next)) return "/";
  const base = "http://carlink.invalid";
  let url: URL;
  try {
    url = new URL(next, base);
  } catch {
    return "/";
  }
  if (url.origin !== base || isPublicPage(url.pathname)) return "/";
  return url.pathname + url.search + url.hash;
}

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const next = safeNext((await searchParams).next);
  // Already signed in (e.g. the Back button): go straight on.
  const user = await getCurrentUser();
  if (user) redirect(user.must_change_password ? "/account" : next);
  return <LoginForm next={next} />;
}
