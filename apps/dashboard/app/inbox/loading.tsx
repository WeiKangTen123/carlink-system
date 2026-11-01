import { PageSkeleton } from "@/components/PageSkeleton";

/** Moves inside the Public Inbox (the list, its tabs, one report) show this
 * at once instead of leaving the old page up until the server answers. */
export default function Loading() {
  return <PageSkeleton />;
}
