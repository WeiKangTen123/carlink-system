"use client";

import { usePathname } from "next/navigation";
import { PageSkeleton, PublicPageSkeleton } from "@/components/PageSkeleton";
import { isPublicPage } from "@/lib/publicPages";

/** Shown while the server renders a page reached from another section
 * (Command Center, Cases Repository, Analytics...). AppShell draws the public
 * pages without the sidebar, so they get a skeleton in their own style; the
 * same path check as AppShell keeps the two in step. */
export default function Loading() {
  const pathname = usePathname();
  return isPublicPage(pathname) ? <PublicPageSkeleton /> : <PageSkeleton />;
}
