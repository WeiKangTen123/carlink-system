import { LoginBrand } from "@/components/LoginBrand";

/** Placeholder the loading.tsx files show while the server renders the next
 * page. Every page fetches fresh data, so without one a click left the old
 * page on screen, looking frozen, until the server answered. "case" roughly
 * follows the case studio: a large panel beside a narrow one. */
export function PageSkeleton({ variant = "page", label = "Loading…" }: { variant?: "page" | "case"; label?: string }) {
  return (
    <div className="page-skeleton" role="status">
      <div className="card-glass page-skeleton-card">
        <div className="skeleton-bar skeleton-title" aria-hidden="true" />
        {variant === "case" ? (
          <div className="page-skeleton-split" aria-hidden="true">
            <div className="skeleton-bar skeleton-block" />
            <div className="page-skeleton-stack">
              <div className="skeleton-bar" />
              <div className="skeleton-bar" />
              <div className="skeleton-bar skeleton-short" />
            </div>
          </div>
        ) : (
          <>
            <div className="page-skeleton-row" aria-hidden="true">
              <div className="skeleton-bar skeleton-block" />
              <div className="skeleton-bar skeleton-block" />
              <div className="skeleton-bar skeleton-block" />
            </div>
            <div className="page-skeleton-row" aria-hidden="true">
              <div className="skeleton-bar skeleton-block" />
              <div className="skeleton-bar skeleton-block" />
              <div className="skeleton-bar skeleton-block" />
            </div>
          </>
        )}
        <span className="page-skeleton-label">{label}</span>
      </div>
    </div>
  );
}

/** The same for the public pages (sign-in, request access, invite links,
 * privacy notice), which have no sidebar and sit in a centred card. */
export function PublicPageSkeleton() {
  return (
    <div className="login-screen" role="status">
      <div className="login-card card-glass page-skeleton page-skeleton-card">
        <LoginBrand />
        <div className="skeleton-bar skeleton-title" aria-hidden="true" />
        <div className="skeleton-bar" aria-hidden="true" />
        <div className="skeleton-bar skeleton-short" aria-hidden="true" />
        <span className="page-skeleton-label">Loading…</span>
      </div>
    </div>
  );
}
