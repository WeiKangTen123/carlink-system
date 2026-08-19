"use client";

import { useState } from "react";
import Link from "next/link";
import { type ReportSummary, fileUrl } from "@/lib/api";
import { ChannelBadge } from "@/components/ChannelBadge";
import { DeleteButton } from "@/components/DeleteButton";

const FALLBACK_CASES: ReportSummary[] = [
  {
    id: "slk-3063-z",
    type: "vehicle_damage",
    status: "Under Review",
    channel: "telegram",
    created_at: new Date().toISOString(),
    location: "Tuas Bay Drive, Singapore",
    category: ["Vehicle Collision", "Rear Impact"],
    thumbnail_url: "/cases/slk3063z/P1273082.JPG",
  },
  {
    id: "vay-4821",
    type: "vehicle_damage",
    status: "Under Review",
    channel: "telegram",
    created_at: new Date(Date.now() - 3600000).toISOString(),
    location: "Federal Highway KM 14.2",
    category: ["Vehicle Collision", "Front Impact"],
    thumbnail_url: "/cases/sample/car_accident_1.jpg",
  },
  {
    id: "wx-8888-a",
    type: "vehicle_damage",
    status: "Signed Off",
    channel: "whatsapp",
    created_at: new Date(Date.now() - 86400000).toISOString(),
    location: "Workshop Bay 2, Subang",
    category: ["Vehicle Collision", "Rear Step"],
    thumbnail_url: "/cases/sample/malaysia_sample_2.jpg",
  },
];

export function ReportsClient({ initialReports }: { initialReports: ReportSummary[] }) {
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const reportsList = initialReports && initialReports.length > 0 ? initialReports : FALLBACK_CASES;

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

  return (
    <div>
      <div
        className="page-header"
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>Vehicle Incident Repository</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Inspect, filter, audit, and sign off vehicle claims cases
          </p>
        </div>
        <Link href="/reports/new" className="button-primary">
          + File New Incident
        </Link>
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
              All ({reportsList.length})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("pending")}
              className={`photo-cat-btn ${statusFilter === "pending" ? "active" : ""}`}
            >
              Pending ({reportsList.filter((r) => r.status !== "Signed Off").length})
            </button>
            <button
              type="button"
              onClick={() => setStatusFilter("signed")}
              className={`photo-cat-btn ${statusFilter === "signed" ? "active" : ""}`}
            >
              Signed Off ({reportsList.filter((r) => r.status === "Signed Off").length})
            </button>
          </div>
        </div>
      </div>

      {/* Reports Table */}
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
                        {r.id.toUpperCase().includes("SLK") ? "CL-11900-SLK3063Z" : `CIR-2026-${r.id.slice(0, 4).toUpperCase()}`}
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
