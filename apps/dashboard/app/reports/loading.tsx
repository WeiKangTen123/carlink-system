import { PageSkeleton } from "@/components/PageSkeleton";

/** app/loading.tsx only covers moving between sections; this covers moves
 * inside Cases Repository (the list, New Intake, a case), which would
 * otherwise leave the old page up until the server answered. */
export default function Loading() {
  return <PageSkeleton />;
}
