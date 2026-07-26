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
    <div style={{ paddingBottom: 40 }}>
      <div className="page-header">
        <div>
          <h1 style={{ margin: 0 }}>System Settings &amp; Administration</h1>
          <p style={{ margin: "4px 0 0", color: "var(--muted)", fontSize: 13 }}>
            Manage user roles, AI Vision model chains, PDF branding, and audit rules
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
        <button
          type="button"
          className={activeTab === "users" ? "button-primary" : ""}
          onClick={() => setActiveTab("users")}
          style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer" }}
        >
          👥 User Roles &amp; Permissions
        </button>
        <button
          type="button"
          className={activeTab === "ai" ? "button-primary" : ""}
          onClick={() => setActiveTab("ai")}
          style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer" }}
        >
          🤖 AI Model &amp; Prompt Config
        </button>
        <button
          type="button"
          className={activeTab === "pdf" ? "button-primary" : ""}
          onClick={() => setActiveTab("pdf")}
          style={{ padding: "8px 16px", borderRadius: 4, cursor: "pointer" }}
        >
          📄 PDF Template Branding
        </button>
      </div>

      {savedMessage && (
        <div style={{ background: "#dcfce7", color: "#166534", padding: "10px 14px", borderRadius: 4, marginBottom: 16 }}>
          ✓ {savedMessage}
        </div>
      )}

      {activeTab === "users" && (
        <div className="card">
          <h2>User Role Management</h2>
          <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 16 }}>
            Define access levels for Admins, Surveyors (Reviewers), and Viewers.
          </p>
          <table className="reports-table">
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
                <td><span className="status-pill" style={{ background: "#2563eb", color: "#fff" }}>Admin</span></td>
                <td>Full access, edit, sign-off, delete, settings management</td>
                <td><span style={{ color: "#16a34a", fontWeight: "bold" }}>Active</span></td>
              </tr>
              <tr>
                <td>
                  <strong>surveyor1@carlink.com</strong>
                </td>
                <td><span className="status-pill">Surveyor / Loss Adjuster</span></td>
                <td>Review AI drafts, edit incident details, sign-off reports</td>
                <td><span style={{ color: "#16a34a", fontWeight: "bold" }}>Active</span></td>
              </tr>
              <tr>
                <td>
                  <strong>viewer@insurance.com</strong>
                </td>
                <td><span className="status-pill" style={{ background: "#e2e8f0", color: "#475569" }}>Viewer</span></td>
                <td>Read-only access, search incidents, download PDF</td>
                <td><span style={{ color: "#16a34a", fontWeight: "bold" }}>Active</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {activeTab === "ai" && (
        <form onSubmit={handleSave} className="card">
          <h2>AI Vision Model Chain Configuration</h2>
          <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 16 }}>
            Configure model fallbacks for continuous 24/7 vision extraction &amp; drafting.
          </p>
          <label style={{ display: "block", marginBottom: 12 }}>
            <strong>Gemini Model Fallback Chain (Comma Separated)</strong>
            <input
              type="text"
              value={modelChain}
              onChange={(e) => setModelChain(e.target.value)}
              style={{ width: "100%", padding: "8px", marginTop: 4, borderRadius: 4, border: "1px solid #ccc" }}
            />
          </label>
          <button type="submit" className="button-primary" style={{ marginTop: 10 }}>
            Save AI Configuration
          </button>
        </form>
      )}

      {activeTab === "pdf" && (
        <form onSubmit={handleSave} className="card">
          <h2>PDF Report Branding &amp; Legal Footer</h2>
          <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 16 }}>
            Customize official PDF report header title, logo, and legal disclaimers.
          </p>
          <label style={{ display: "block", marginBottom: 12 }}>
            <strong>Company / Workshop Name</strong>
            <input
              type="text"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              style={{ width: "100%", padding: "8px", marginTop: 4, borderRadius: 4, border: "1px solid #ccc" }}
            />
          </label>
          <button type="submit" className="button-primary" style={{ marginTop: 10 }}>
            Save PDF Branding
          </button>
        </form>
      )}
    </div>
  );
}
