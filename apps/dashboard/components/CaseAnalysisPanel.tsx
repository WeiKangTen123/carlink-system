import type { ReportDetail, DamageSummaryItem } from "@/lib/api";
import { severityClass } from "./StudioApp";

/** A completeness ratio shown as a meter, not a chart -- a single ratio
 * against a limit is exactly the case where a bar of one value beats a
 * pie or a one-bar chart. Sequential (one hue) because this is magnitude
 * toward a target, not identity. */
function ReadinessMeter({
  label,
  done,
  total,
  hint,
}: {
  label: string;
  done: number;
  total: number;
  hint?: string;
}) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const complete = total > 0 && done === total;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4, gap: 12 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{label}</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)", flexShrink: 0 }}>
          {done}/{total}
          {complete ? " ✓" : hint ? ` · ${hint}` : ""}
        </span>
      </div>
      <div className="severity-bar-track">
        <div
          className="readiness-bar-fill"
          style={{ width: `${pct}%`, background: complete ? "var(--badge-green-text)" : "var(--accent-cyan)" }}
        />
      </div>
    </div>
  );
}

function StatBlock({ value, label, tone }: { value: string | number; label: string; tone?: string }) {
  return (
    <div className="case-stat-block">
      <div className="case-stat-value" style={tone ? { color: tone } : undefined}>{value}</div>
      <div className="case-stat-label">{label}</div>
    </div>
  );
}

/** Per-case analysis: everything here is counted from this report's own
 * real damage_summary fields. No projections, no scores -- the point is
 * to answer "is this case actually ready to quote / sign off, and what
 * does the job involve", which nothing in the app could tell you before.
 */
export function CaseAnalysisPanel({
  report,
  damageEntries,
}: {
  report: ReportDetail;
  damageEntries: DamageSummaryItem[];
}) {
  const total = damageEntries.length;

  const severityCounts = { severe: 0, moderate: 0, minor: 0, unrated: 0 };
  let verified = 0;
  let withOem = 0;
  let photoLinked = 0;
  let repairRequired = 0;

  damageEntries.forEach((item) => {
    if (!item.severity) severityCounts.unrated++;
    else severityCounts[severityClass(item.severity) as "severe" | "moderate" | "minor"]++;
    if (item.human_verified) verified++;
    if (item.oem_part_number) withOem++;
    if (item.photo_reference) photoLinked++;
    if (item.repair_required) repairRequired++;
  });

  const daysOpen = Math.max(
    0,
    Math.floor((Date.now() - new Date(report.created_at).getTime()) / 86400000)
  );
  const cost = report.data.insurance_details?.estimated_repair_cost;

  // Damage types get a table rather than a chart on purpose: real
  // assessor text produces many one-off categories ("Grazed/slack/cut"),
  // well past the point where extra colours help rather than hurt.
  const typeCounts = new Map<string, number>();
  damageEntries.forEach((item) => {
    if (!item.damage_type) return;
    typeCounts.set(item.damage_type, (typeCounts.get(item.damage_type) ?? 0) + 1);
  });
  const topTypes = [...typeCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);

  // Plain statement of what's actually missing, rather than a composite
  // "readiness score" that would imply more precision than these three
  // counts support.
  const blockers: string[] = [];
  if (total === 0) blockers.push("no damage items recorded");
  if (total > 0 && verified < total) blockers.push(`${total - verified} part${total - verified === 1 ? "" : "s"} not verified`);
  if (total > 0 && withOem < total) blockers.push(`${total - withOem} missing an OEM number`);
  if (!cost) blockers.push("no cost estimate recorded");

  if (total === 0) {
    return (
      <div className="card-glass">
        <div className="card-header">
          <div className="card-title">
            <span>📊</span> Case Analysis
          </div>
        </div>
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
          No damage items recorded on this case yet — nothing to analyse.
        </p>
      </div>
    );
  }

  const severityBars = [
    { key: "severe", count: severityCounts.severe, color: "var(--chart-severe)", label: "Severe" },
    { key: "moderate", count: severityCounts.moderate, color: "var(--chart-moderate)", label: "Moderate" },
    { key: "minor", count: severityCounts.minor, color: "var(--chart-minor)", label: "Minor" },
    { key: "unrated", count: severityCounts.unrated, color: "var(--text-muted)", label: "Unrated" },
  ].filter((s) => s.count > 0);

  return (
    <div className="card-glass">
      <div className="card-header">
        <div>
          <div className="card-title">
            <span>📊</span> Case Analysis
          </div>
          <div className="card-subtitle">Job scope and whether this case is ready to quote</div>
        </div>
      </div>

      {/* Headline numbers as stat tiles -- single values, so not charts. */}
      <div className="case-stat-row">
        <StatBlock value={total} label={total === 1 ? "damaged part" : "damaged parts"} />
        <StatBlock
          value={severityCounts.severe}
          label="rated severe"
          tone={severityCounts.severe > 0 ? "var(--badge-red-text)" : undefined}
        />
        <StatBlock value={cost || "—"} label="estimated cost" tone={cost ? "var(--accent-cyan)" : undefined} />
        <StatBlock value={daysOpen === 0 ? "today" : `${daysOpen}d`} label="open" />
      </div>

      {/* Part-to-whole -> a single stacked bar, not a pie. */}
      <div style={{ marginTop: 20 }}>
        <div className="detail-field-label" style={{ marginBottom: 8 }}>Severity mix</div>
        <div className="stacked-severity-bar">
          {severityBars.map((s) => (
            <div
              key={s.key}
              style={{ width: `${(s.count / total) * 100}%`, background: s.color }}
              title={`${s.label}: ${s.count}`}
            />
          ))}
        </div>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 8 }}>
          {severityBars.map((s) => (
            <span key={s.key} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, color: "var(--text-muted)" }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: s.color, display: "inline-block" }} />
              {s.label} · {s.count}
            </span>
          ))}
        </div>
      </div>

      <div className="case-analysis-split">
        <div>
          <div className="detail-field-label" style={{ marginBottom: 10 }}>Assessment readiness</div>
          <ReadinessMeter label="Parts verified" done={verified} total={total} hint="pending" />
          <ReadinessMeter label="OEM numbers recorded" done={withOem} total={total} hint="missing" />
          <ReadinessMeter label="Linked to a photo" done={photoLinked} total={total} hint="unlinked" />

          {blockers.length === 0 ? (
            <p className="case-analysis-verdict ready">✓ Fully assessed — every part verified with an OEM number and a cost on file.</p>
          ) : (
            <p className="case-analysis-verdict blocked">
              Not yet quote-ready: {blockers.join(", ")}.
            </p>
          )}
        </div>

        <div>
          <div className="detail-field-label" style={{ marginBottom: 10 }}>
            Damage types {typeCounts.size > 6 && <span style={{ color: "var(--text-muted)" }}>(top 6 of {typeCounts.size})</span>}
          </div>
          {topTypes.length === 0 ? (
            <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>No damage types recorded.</p>
          ) : (
            <table className="mini-table">
              <tbody>
                {topTypes.map(([type, count]) => (
                  <tr key={type}>
                    <td>{type}</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="settings-info-row" style={{ marginTop: 12 }}>
            <span className="settings-info-label">Parts flagged for repair</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, fontWeight: 600 }}>
              {repairRequired} of {total}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
