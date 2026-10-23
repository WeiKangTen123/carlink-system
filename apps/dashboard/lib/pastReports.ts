/** Small helpers shared by the past-report features: the Annex A
 * suggestions on a case (components/AnnexSuggestionsPanel.tsx) and the
 * parts price history on the Knowledge page. */

/** Line wording without case, spacing or punctuation -- how a suggested
 * line is matched against the lines already in a table. */
export function sameLineKey(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export function formatSgd(n: number): string {
  return `S$${n.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Past report dates are ISO ("2025-03-14"); shown as "14 Mar 2025"
 * without going through a time zone, so server and browser agree. */
export function shortIsoDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const month = m ? MONTHS[Number(m[2]) - 1] : undefined;
  return m && month ? `${Number(m[3])} ${month} ${m[1]}` : iso;
}
