import Link from "next/link";
import { FileQuestion, FileText, Home, FilePlus2, Search } from "lucide-react";

export default function NotFound() {
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
          padding: "48px 36px",
          textAlign: "center",
          boxShadow: "0 20px 40px -15px rgba(0, 0, 0, 0.35)",
        }}
      >
        {/* Radar / Not found icon */}
        <div
          style={{
            width: 64,
            height: 64,
            borderRadius: "20px",
            background: "rgba(14, 165, 233, 0.12)",
            border: "1px solid rgba(14, 165, 233, 0.3)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            margin: "0 auto 20px",
            color: "var(--accent-cyan)",
            boxShadow: "0 0 24px rgba(14, 165, 233, 0.2)",
          }}
        >
          <FileQuestion style={{ width: 32, height: 32 }} />
        </div>

        <div
          style={{
            fontSize: 11,
            fontWeight: 700,
            color: "var(--accent-cyan)",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            marginBottom: 8,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 6,
          }}
        >
          <Search style={{ width: 12, height: 12 }} />
          Loss Adjuster Registry &bull; 404 Unlocated
        </div>

        <h1
          style={{
            fontSize: 24,
            fontWeight: 800,
            marginBottom: 12,
            letterSpacing: "-0.02em",
            color: "var(--text-primary)",
          }}
        >
          Incident Case Not Found
        </h1>

        <p
          style={{
            fontSize: 13,
            color: "var(--text-muted)",
            lineHeight: 1.6,
            maxWidth: 440,
            margin: "0 auto 28px",
          }}
        >
          The vehicle inspection dossier or report reference you requested does not exist, has been expunged, or the identifier is invalid.
        </p>

        <div
          style={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <Link
            href="/reports"
            className="btn-primary-modern"
            style={{ padding: "10px 22px", fontSize: 13 }}
          >
            <FileText style={{ width: 15, height: 15 }} /> Return to Cases Console
          </Link>

          <Link
            href="/"
            className="btn-secondary-modern"
            style={{ padding: "10px 18px", fontSize: 13 }}
          >
            <Home style={{ width: 14, height: 14 }} /> Surveyor Overview
          </Link>

          <Link
            href="/reports/new"
            className="btn-secondary-modern"
            style={{ padding: "10px 18px", fontSize: 13 }}
          >
            <FilePlus2 style={{ width: 14, height: 14 }} /> File New Intake
          </Link>
        </div>
      </div>
    </div>
  );
}
