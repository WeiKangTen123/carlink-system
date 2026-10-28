import { PageSkeleton } from "@/components/PageSkeleton";

/** Moving between the Settings tabs: the header and tab bar (layout.tsx)
 * stay put and this fills in below them until the server answers. */
export default function Loading() {
  return <PageSkeleton />;
}
