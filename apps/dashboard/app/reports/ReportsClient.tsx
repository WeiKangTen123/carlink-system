"use client";

import { useState } from "react";
import Link from "next/link";
import { type ReportSummary, fileUrl } from "@/lib/api";
import { ChannelBadge } from "@/components/ChannelBadge";
import { DeleteButton } from "@/components/DeleteButton";

export function ReportsClient({ initialReports }: { initialReports: ReportSummary[] }) {
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const filteredReports = initialReports.filter((r) => {
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

  return (
    <div>
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24 }}>Car Incident Reports</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Manage, search, review, and sign-off vehicle incident reports
          </p>
        </div>
        <Link href="/reports/new" className="button-primary">
          + File New Report
        </Link>
      </div>

      {/* Search & Filter Controls Card */}
      <div className="card" style={{ marginBottom: 20, padding: "16px 20px" }}>
        <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          {/* Search Box */}
          <div style={{ flex: 1, minWidth: 240, position: "relative" }}>
            <input
              type="text"
              placeholder="🔍 Search by Report ID, Location, Category..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              style={{
                width: "100%",
                padding: "10px 14px",
                borderRadius: 8,
                border: "1px solid var(--border-color)",
                background: "var(--surface-card)",
                color: "var(--text-primary)",
                fontSize: 14,
              }}
            />
          </div>

          {/* Filter Tabs */}
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => setStatusFilter("all")}
              style={{
                padding: "8px 14px",
                borderRadius: 6,
                border: "1px solid var(--border-color)",
                background: statusFilter === "all" ? "var(--accent-primary)" : "var(--surface-card)",
                color: statusFilter === "all" ? "#fff" : "var(--text-primary)",
                fontWeight: 600,
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              All ({initialReports.length})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("pending")}
              style={{
                padding: "8px 14px",
                borderRadius: 6,
                border: "1px solid var(--border-color)",
                background: statusFilter === "pending" ? "var(--accent-primary)" : "var(--surface-card)",
                color: statusFilter === "pending" ? "#fff" : "var(--text-primary)",
                fontWeight: 600,
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              Pending Review ({initialReports.filter((r) => r.status !== "Signed Off").length})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("signed")}
              style={{
                padding: "8px 14px",
                borderRadius: 6,
                border: "1px solid var(--border-color)",
                background: statusFilter === "signed" ? "var(--accent-primary)" : "var(--surface-card)",
                color: statusFilter === "signed" ? "#fff" : "var(--text-primary)",
                fontWeight: 600,
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              Signed Off ({initialReports.filter((r) => r.status === "Signed Off").length})
            </button>
          </div>
        </div>
      </div>

      {/* Reports Table */}
      <div className="card">
        <div style={{ overflowX: "auto" }}>
          <table className="reports-table">
            <thead>
              <tr>
                <th>Photo</th>
                <th>Report ID / Date</th>
                <th>Channel</th>
                <th>Location</th>
                <th>Category / Classification</th>
                <th>Status</th>
                <th style={{ textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredReports.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link href={`/reports/${r.id}`}>
                      {r.thumbnail_url ? (
                        <img src={fileUrl(r.thumbnail_url)} alt="" className="row-thumb" />
                      ) : (
                        <span className="row-thumb row-thumb-empty">No Photo</span>
                      )}
                    </Link>
                  </td>
                  <td>
                    <div>
                      <strong style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                        CIR-2026-{r.id.slice(0, 4).toUpperCase()}
                      </strong>
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
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
                      <span className="tag">Vehicle Collision or Damage</span>
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
                      style={{
                        fontWeight: 600,
                        marginRight: 12,
                        color: "var(--accent-primary)",
                      }}
                    >
                      Review &amp; Edit →
                    </Link>
                    <DeleteButton id={r.id} />
                  </td>
                </tr>
              ))}
              {filteredReports.length === 0 && (
                <tr>
                  <td colSpan={7} style={{ textAlign: "center", padding: 24, color: "var(--text-muted)" }}>
                    No incident reports match your search criteria.
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
