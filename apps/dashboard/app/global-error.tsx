"use client";

import React, { useEffect } from "react";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    console.error("Global Root Layout Crash captured by Carlink:", error);
  }, [error]);

  return (
    <html lang="en" data-theme="dark">
      <head>
        <title>Carlink Loss Adjuster &bull; System Recovery</title>
      </head>
      <body
        style={{
          margin: 0,
          padding: "24px 16px",
          minHeight: "100vh",
          backgroundColor: "#070a12",
          color: "#f1f5f9",
          fontFamily:
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            maxWidth: 540,
            width: "100%",
            backgroundColor: "#0f172a",
            borderRadius: 16,
            border: "1px solid rgba(239, 68, 68, 0.3)",
            padding: "44px 32px",
            textAlign: "center",
            boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.7), 0 0 30px rgba(239, 68, 68, 0.15)",
          }}
        >
          {/* Recovery Icon */}
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: "50%",
              backgroundColor: "rgba(239, 68, 68, 0.15)",
              border: "1px solid rgba(239, 68, 68, 0.35)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 20px",
              color: "#ef4444",
              fontSize: 24,
            }}
          >
            &#9888;
          </div>

          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              color: "#ef4444",
              textTransform: "uppercase",
              letterSpacing: "0.12em",
              marginBottom: 8,
            }}
          >
            Carlink Core Recovery &bull; Root Fault Isolated
          </div>

          <h1
            style={{
              fontSize: 22,
              fontWeight: 800,
              margin: "0 0 12px",
              color: "#ffffff",
              letterSpacing: "-0.02em",
            }}
          >
            Root Application Interruption
          </h1>

          <p
            style={{
              fontSize: 13,
              color: "#94a3b8",
              lineHeight: 1.6,
              margin: "0 0 28px",
            }}
          >
            A catastrophic layout error was intercepted before causing a blank screen crash. You can attempt to reload the core session or return to the main dashboard.
          </p>

          <div
            style={{
              display: "flex",
              gap: 12,
              justifyContent: "center",
              alignItems: "center",
              flexWrap: "wrap",
              marginBottom: 20,
            }}
          >
            <button
              type="button"
              onClick={() => reset()}
              style={{
                backgroundColor: "#2563eb",
                color: "#ffffff",
                border: "none",
                borderRadius: 8,
                padding: "10px 22px",
                fontSize: 13,
                fontWeight: 700,
                cursor: "pointer",
                boxShadow: "0 4px 14px rgba(37, 99, 235, 0.4)",
              }}
            >
              Restart Session
            </button>

            <a
              href="/"
              style={{
                backgroundColor: "#1e293b",
                color: "#cbd5e1",
                border: "1px solid #334155",
                borderRadius: 8,
                padding: "10px 20px",
                fontSize: 13,
                fontWeight: 600,
                textDecoration: "none",
              }}
            >
              Return Home
            </a>
          </div>

          {error?.message && (
            <div
              style={{
                marginTop: 20,
                padding: "10px 14px",
                backgroundColor: "#090d16",
                borderRadius: 8,
                border: "1px solid #1e293b",
                fontSize: 11,
                fontFamily: "monospace",
                color: "#94a3b8",
                wordBreak: "break-all",
                textAlign: "left",
              }}
            >
              <span style={{ color: "#f87171" }}>Exception:</span> {error.message}
            </div>
          )}
        </div>
      </body>
    </html>
  );
}
