/** Opening a case while the server loads the report: the shape of the case
 * page (header, the five tabs, the blueprint beside the photos) so the
 * click doesn't look frozen. Every page fetches fresh data, so without a
 * placeholder the old page stayed on screen until the server answered. */
export default function Loading() {
  return (
    <div className="page-skeleton" role="status">
      <div className="card-glass page-skeleton-card">
        <div className="skeleton-bar skeleton-title" aria-hidden="true" />
        <div className="skeleton-bar skeleton-short" aria-hidden="true" />
        <div className="case-skeleton-tabs" aria-hidden="true">
          <div className="skeleton-bar" />
          <div className="skeleton-bar" />
          <div className="skeleton-bar" />
          <div className="skeleton-bar" />
          <div className="skeleton-bar" />
        </div>
        <div className="page-skeleton-split" aria-hidden="true">
          <div className="skeleton-bar skeleton-block" />
          <div className="page-skeleton-stack">
            <div className="skeleton-bar skeleton-block" />
            <div className="skeleton-bar" />
            <div className="skeleton-bar skeleton-short" />
          </div>
        </div>
        <span className="page-skeleton-label">Loading case…</span>
      </div>
    </div>
  );
}
