"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  RotateCcw,
  Home,
  FileText,
  ChevronDown,
  ChevronUp,
  ShieldAlert,
} from "lucide-react";

interface ErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function ErrorBoundary({ error, reset }: ErrorProps) {
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    // Log the error to console or upstream monitoring
    console.error("Carlink System Error Boundary caught:", error);
  }, [error]);

  return (
    <div
      style={{
        minHeight: "70vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "32px 16px",
      }}
    >
      <div
        className="card-glass"
        style={{
          maxWidth: 580,
          width: "100%",
          padding: "40px 32px",
          textAlign: "center",
          boxShadow: "0 20px 40px -15px rgba(0, 0, 0, 0.4)",
          border: "1px solid rgba(239, 68, 68, 0.25)",
        }}
      >
        {/* Pulsing error indicator icon */}
        <div
          style={{
            width: 60,
            height: 60,
            borderRadius: "16px",
            background: "rgba(239, 68, 68, 0.12)",
            border: "1px solid rgba(239, 68, 68, 0.3)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            margin: "0 auto 20px",
            color: "#ef4444",
            boxShadow: "0 0 24px rgba(239, 68, 68, 0.2)",
          }}
        >
          <ShieldAlert style={{ width: 30, height: 30 }} />
        </div>

        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: "#ef4444",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            marginBottom: 8,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
          }}
        >
          <AlertTriangle style={{ width: 12, height: 12 }} />
          Carlink Resilience Layer &bull; Session Interrupted
        </div>

        <h1
          style={{
            fontSize: 22,
            fontWeight: 800,
            marginBottom: 12,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}
        >
          Service Communication Anomaly
        </h1>

        <p
          style={{
            fontSize: 13,
            color: "var(--text-muted)",
            lineHeight: 1.6,
            maxWidth: 460,
            margin: "0 auto 24px",
          }}
        >
          An unexpected runtime condition occurred while rendering this view or communicating with the backend API. The resilience boundary has safely isolated the incident.
        </p>

        {/* Action button cluster */}
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
            marginBottom: 24,
          }}
        >
          <button
            type="button"
            onClick={() => reset()}
            className="btn-primary-modern"
            style={{ padding: "10px 20px", fontSize: 13 }}
          >
            <RotateCcw style={{ width: 14, height: 14 }} /> Try Again
          </button>

          <Link
            href="/"
            className="btn-secondary-modern"
            style={{ padding: "10px 18px", fontSize: 13 }}
          >
            <Home style={{ width: 14, height: 14 }} /> Return to Overview
          </Link>

          <Link
            href="/reports"
            className="btn-secondary-modern"
            style={{ padding: "10px 18px", fontSize: 13 }}
          >
            <FileText style={{ width: 14, height: 14 }} /> Triage Console
          </Link>
        </div>

        {/* Technical diagnostics toggle */}
        <div
          style={{
            borderTop: "1px solid var(--border-color)",
            paddingTop: 16,
            marginTop: 8,
          }}
        >
          <button
            type="button"
            onClick={() => setShowDetails(!showDetails)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text-muted)",
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "4px 8px",
            }}
          >
            {showDetails ? (
              <>
                <ChevronUp style={{ width: 13, height: 13 }} /> Hide Diagnostics
              </>
            ) : (
              <>
                <ChevronDown style={{ width: 13, height: 13 }} /> View Diagnostics
              </>
            )}
          </button>

          {showDetails && (
            <div
              style={{
                marginTop: 12,
                padding: "12px 14px",
                background: "var(--surface-elevated)",
                border: "1px solid var(--border-color)",
                borderRadius: "8px",
                textAlign: "left",
                fontFamily: "var(--font-mono, monospace)",
                fontSize: 11,
                color: "var(--text-secondary)",
                lineHeight: 1.5,
                wordBreak: "break-all",
                maxHeight: 160,
                overflowY: "auto",
              }}
            >
              <div style={{ color: "#ef4444", fontWeight: 700, marginBottom: 4 }}>
                {error.name || "Error"}: {error.message || "Unknown error"}
              </div>
              {error.digest && (
                <div style={{ color: "var(--text-muted)", fontSize: 10 }}>
                  Digest: {error.digest}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
