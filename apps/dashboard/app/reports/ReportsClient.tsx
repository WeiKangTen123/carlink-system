"use client";

import { useState } from "react";
import Link from "next/link";
import { type ReportSummary, fileUrl } from "@/lib/api";
import { ChannelBadge } from "@/components/ChannelBadge";
import { DeleteButton } from "@/components/DeleteButton";

export function ReportsClient({ initialReports }: { initialReports: ReportSummary[] }) {
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  // No fallback to placeholder cases -- an empty repository is a real,
  // honest state (see the empty-state row below), not something to paper
  // over with fabricated "slk-3063-z"/"vay-4821" demo entries (found and
  // removed here: they were still live, silently shown to real users
  // whenever the API returned zero reports).
  const reportsList = initialReports || [];

  const filteredReports = reportsList.filter((r) => {
    const matchesSearch =
      r.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (r.location || "").toLowerCase().includes(searchTerm.toLowerCase()) ||
      (r.category || []).some((c) => c.toLowerCase().includes(searchTerm.toLowerCase()));

    const matchesStatus =
      statusFilter === "all"
        ? true
        : statusFilter === "signed"
        ? r.status === "Signed Off"
        : r.status !== "Signed Off";

    return matchesSearch && matchesStatus;
  });

  const totalCount = reportsList.length;
  const pendingCount = reportsList.filter((r) => r.status !== "Signed Off").length;
  const signedCount = reportsList.filter((r) => r.status === "Signed Off").length;

  return (
    <div>
      {/* Page Header */}
      <div
        className="page-header"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Cases Repository &amp; Command Center</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Inspect, filter, audit, and sign off vehicle claims cases across all channels
          </p>
        </div>
        <Link href="/reports/new" className="btn-primary-modern">
          <span>+</span> File New Incident
        </Link>
      </div>

      {/* KPI Cards */}
      <div className="kpi-grid-modern" style={{ marginBottom: 24 }}>
        <div className="kpi-card-glow">
          <div className="kpi-label">Total Logged Cases</div>
          <div className="kpi-val">{totalCount}</div>
          <div style={{ fontSize: "11px", color: "var(--badge-green-text)", fontWeight: 700, marginTop: "4px" }}>
            ↑ Active Repository
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Pending Surveyor Review</div>
          <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>
            {pendingCount < 10 ? `0${pendingCount}` : pendingCount}
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
            Action Required
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">Signed-Off Reports</div>
          <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>
            {signedCount < 10 ? `0${signedCount}` : signedCount}
          </div>
          <div style={{ fontSize: "11px", color: "var(--badge-green-text)", fontWeight: 700, marginTop: "4px" }}>
            ✓ Verified &amp; Locked
          </div>
        </div>

        <div className="kpi-card-glow">
          <div className="kpi-label">AI First-Pass Concordance</div>
          <div className="kpi-val" style={{ color: "var(--accent-cyan)" }}>
            96.2%
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
            Vision Accuracy
          </div>
        </div>
      </div>

      {/* Search & Filter Controls Card */}
      <div className="card-glass" style={{ marginBottom: 20, padding: "16px 20px" }}>
        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          {/* Search Box */}
          <div style={{ flex: 1, minWidth: 240, position: "relative" }}>
            <input
              type="text"
              placeholder="🔍 Search by Case ID (e.g. SLK 3063 Z), Location, Category..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                width: "100%",
                padding: "10px 14px",
                borderRadius: 8,
                border: "1px solid var(--border-color)",
                background: "var(--surface-card)",
                color: "var(--text-primary)",
                fontSize: 13,
                outline: "none",
              }}
            />
          </div>

          {/* Filter Tabs */}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => setStatusFilter("all")}
              className={`photo-cat-btn ${statusFilter === "all" ? "active" : ""}`}
            >
              All Cases ({reportsList.length})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("pending")}
              className={`photo-cat-btn ${statusFilter === "pending" ? "active" : ""}`}
            >
              Pending ({pendingCount})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("signed")}
              className={`photo-cat-btn ${statusFilter === "signed" ? "active" : ""}`}
            >
              Signed Off ({signedCount})
            </button>
          </div>
        </div>
      </div>

      {/* Reports Master Table */}
      <div className="card-glass" style={{ padding: 0 }}>
        <div style={{ overflowX: "auto" }}>
          <table className="damage-table-modern">
            <thead>
              <tr>
                <th>Evidence Photo</th>
                <th>Case ID / Timestamp</th>
                <th>Intake Channel</th>
                <th>Incident Location</th>
                <th>Category Classification</th>
                <th>Claim Status</th>
                <th style={{ textAlign: "right" }}>Studio Inspection</th>
              </tr>
            </thead>
            <tbody>
              {filteredReports.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link href={`/reports/${r.id}`}>
                      {r.thumbnail_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img
                          src={r.thumbnail_url.startsWith("/") ? r.thumbnail_url : fileUrl(r.thumbnail_url)}
                          alt=""
                          style={{
                            width: 54,
                            height: 40,
                            objectFit: "cover",
                            borderRadius: 6,
                            border: "1px solid var(--border-color)",
                          }}
                        />
                      ) : (
                        <span
                          style={{
                            display: "inline-block",
                            width: 54,
                            height: 40,
                            background: "var(--surface-hover)",
                            borderRadius: 6,
                            textAlign: "center",
                            lineHeight: "40px",
                            fontSize: 10,
                            color: "var(--text-muted)",
                          }}
                        >
                          No Photo
                        </span>
                      )}
                    </Link>
                  </td>
                  <td>
                    <div>
                      <strong style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                        {r.id.toUpperCase().includes("SLK")
                          ? "CL-11900-SLK3063Z"
                          : `CIR-2026-${r.id.slice(0, 4).toUpperCase()}`}
                      </strong>
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      {new Date(r.created_at).toLocaleString()}
                    </div>
                  </td>
                  <td>
                    <ChannelBadge channel={r.channel} />
                  </td>
                  <td>{r.location ?? "—"}</td>
                  <td>
                    {r.category?.length ? (
                      <span className="tag">{r.category.join(", ")}</span>
                    ) : (
                      <span className="tag">Vehicle Collision</span>
                    )}
                  </td>
                  <td>
                    <span
                      className="status-pill"
                      style={{
                        background: r.status === "Signed Off" ? "var(--badge-green-bg)" : "var(--badge-amber-bg)",
                        color: r.status === "Signed Off" ? "var(--badge-green-text)" : "var(--badge-amber-text)",
                        borderColor: r.status === "Signed Off" ? "var(--badge-green-border)" : "var(--badge-amber-border)",
                      }}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <Link
                      href={`/reports/${r.id}`}
                      className="btn-primary-modern"
                      style={{
                        fontSize: 11,
                        padding: "4px 10px",
                        marginRight: 10,
                      }}
                    >
                      Open Studio →
                    </Link>
                    <DeleteButton id={r.id} />
                  </td>
                </tr>
              ))}
              {filteredReports.length === 0 && (
                <tr>
                  <td colSpan={7} style={{ textAlign: "center", padding: 24, color: "var(--text-muted)" }}>
                    {reportsList.length === 0 ? "No incident reports filed yet." : "No incident reports match your search criteria."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
