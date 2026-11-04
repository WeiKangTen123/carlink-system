/** Small helpers shared by the past-report features: the Annex A
 * suggestions on a case (components/AnnexSuggestionsPanel.tsx) and the
 * parts price history on the Knowledge page. */

/** Line wording without case, spacing or punctuation -- how a suggested
 * line is matched against the lines already in a table. */
export function sameLineKey(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/** The same for an Annex B job description, also without the "To " the
 * firm starts most lines with ("To remove, reinstall tail gate parts" and
 * "Remove & reinstall tail gate parts" are one line) -- the key the API
 * groups past labour lines by (app/parts/history.py suggest_labour). */
export function sameLabourKey(description: string): string {
  return sameLineKey(description).replace(/^to /, "");
}

/** A quantity as a surveyor writes it ("1set", "2", "10 pcs"): the leading
 * number, or 0 when there is none. */
export function leadingNumber(text: string): number {
  const m = text.trim().match(/^(\d+(?:\.\d+)?)/);
  const n = m ? parseFloat(m[1]) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function formatSgd(n: number): string {
  return `S$${n.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** "7,475.04": how the firm prints money in its tables (S$ only in the letter). */
export function formatAmount(n: number): string {
  return n.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
