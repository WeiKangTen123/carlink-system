"use client";

import { useState } from "react";

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<"users" | "ai" | "pdf">("users");
  const [companyName, setCompanyName] = useState("Carlink Automotive & Incident System");
  const [modelChain, setModelChain] = useState("gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash");
  const [savedMessage, setSavedMessage] = useState("");

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    setSavedMessage("Settings saved successfully!");
    setTimeout(() => setSavedMessage(""), 3000);
  };

  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, fontWeight: 800 }}>System Settings &amp; Configuration</h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Manage surveyor user roles, AI Gemini fallback model chains, and official PDF certificate branding
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
        <button
          type="button"
          className={`photo-cat-btn ${activeTab === "users" ? "active" : ""}`}
          onClick={() => setActiveTab("users")}
        >
          👥 User Roles &amp; Permissions
        </button>
        <button
          type="button"
          className={`photo-cat-btn ${activeTab === "ai" ? "active" : ""}`}
          onClick={() => setActiveTab("ai")}
        >
          🤖 AI Model &amp; Prompt Chain
        </button>
        <button
          type="button"
          className={`photo-cat-btn ${activeTab === "pdf" ? "active" : ""}`}
          onClick={() => setActiveTab("pdf")}
        >
          📄 PDF Report Branding
        </button>
      </div>

      {savedMessage && (
        <div
          style={{
            background: "var(--badge-green-bg)",
            color: "var(--badge-green-text)",
            border: "1px solid var(--badge-green-border)",
            padding: "10px 16px",
            borderRadius: 8,
            marginBottom: 20,
            fontSize: 13,
            fontWeight: 700,
          }}
        >
          ✓ {savedMessage}
        </div>
      )}

      {activeTab === "users" && (
        <div className="card-glass" style={{ padding: 0 }}>
          <div style={{ padding: "20px 24px", borderBottom: "1px solid var(--border-color)" }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>User Role Management</h2>
            <p style={{ color: "var(--text-muted)", fontSize: 13, margin: "4px 0 0" }}>
              Define access levels for System Admins, Loss Adjuster Surveyors, and Insurance Viewers.
            </p>
          </div>

          <table className="damage-table-modern">
            <thead>
              <tr>
                <th>User / Email</th>
                <th>Role</th>
                <th>Permissions</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <strong>weikang@carlink.com</strong>
                </td>
                <td>
                  <span className="chip-severity" style={{ background: "var(--accent-cyan)", color: "#ffffff" }}>
                    Admin
                  </span>
                </td>
                <td>Full access, edit, sign-off, delete, settings management</td>
                <td>
                  <span style={{ color: "var(--badge-green-text)", fontWeight: "bold" }}>● Active</span>
                </td>
              </tr>
              <tr>
                <td>
                  <strong>alex.wong@carlink.com</strong>
                </td>
                <td>
                  <span className="chip-severity minor">Loss Adjuster / Surveyor</span>
                </td>
                <td>Review AI drafts, edit incident details, sign-off reports</td>
                <td>
                  <span style={{ color: "var(--badge-green-text)", fontWeight: "bold" }}>● Active</span>
                </td>
              </tr>
              <tr>
                <td>
                  <strong>viewer@insurance.com</strong>
                </td>
                <td>
                  <span className="chip-severity minor">Claims Adjuster</span>
                </td>
                <td>Read-only access, search incidents, download PDF</td>
                <td>
                  <span style={{ color: "var(--badge-green-text)", fontWeight: "bold" }}>● Active</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {activeTab === "ai" && (
        <form onSubmit={handleSave} className="card-glass">
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>AI Vision Model Chain Configuration</h2>
            <p style={{ color: "var(--text-muted)", fontSize: 13, margin: "4px 0 0" }}>
              Configure model fallbacks for continuous 24/7 multimodal vision extraction &amp; drafting.
            </p>
          </div>

          <label style={{ display: "block", marginBottom: 16 }}>
            <span className="detail-field-label">Gemini Model Fallback Chain (Comma Separated)</span>
            <input
              type="text"
              value={modelChain}
              onChange={(e) => setModelChain(e.target.value)}
              style={{
                width: "100%",
                padding: "10px 14px",
                marginTop: 6,
                borderRadius: 8,
                border: "1px solid var(--border-color)",
                background: "var(--surface-card)",
                color: "var(--text-primary)",
                fontFamily: "var(--font-mono)",
                fontSize: 13,
              }}
            />
          </label>

          <button type="submit" className="btn-primary-modern">
            Save AI Configuration
          </button>
        </form>
      )}

      {activeTab === "pdf" && (
        <form onSubmit={handleSave} className="card-glass">
          <div style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>PDF Report Branding &amp; Legal Header</h2>
            <p style={{ color: "var(--text-muted)", fontSize: 13, margin: "4px 0 0" }}>
              Customize official PDF report header title, company name, and legal disclaimers.
            </p>
          </div>

          <label style={{ display: "block", marginBottom: 16 }}>
            <span className="detail-field-label">Company / Workshop Name</span>
            <input
              type="text"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              style={{
                width: "100%",
                padding: "10px 14px",
                marginTop: 6,
                borderRadius: 8,
                border: "1px solid var(--border-color)",
                background: "var(--surface-card)",
                color: "var(--text-primary)",
                fontSize: 13,
              }}
            />
          </label>

          <button type="submit" className="btn-primary-modern">
            Save PDF Branding
          </button>
        </form>
      )}
    </div>
  );
}
