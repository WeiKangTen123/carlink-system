"use client";

import { useState, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { FileText, FileDown, X } from "lucide-react";
import { pdfDownloadUrl, API_BASE_URL } from "@/lib/api";

export function PdfPreviewModal({
  reportId,
  pdfUrl,
  reportCode,
  autoOpen = false,
}: {
  reportId: string;
  pdfUrl?: string | null;
  reportCode: string;
  autoOpen?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(autoOpen);
  const [wasAutoOpened] = useState(autoOpen);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (autoOpen) {
      router.replace(pathname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    if (isOpen) window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  const viewUrl = pdfUrl ? (pdfUrl.startsWith("http") ? pdfUrl : `${API_BASE_URL}${pdfUrl}`) : pdfDownloadUrl(reportId);
  const downloadUrl = pdfDownloadUrl(reportId);
  const fileName = `Car_Incident_Report_${reportCode}.pdf`;

  return (
    <>
      <button
        type="button"
        onClick={() => setIsOpen(true)}
        className="btn-secondary-modern"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
        }}
      >
        <FileText style={{ width: 14, height: 14, color: "var(--accent-primary)" }} />
        <span>Preview &amp; Download PDF</span>
      </button>

      {isOpen && (
        <div
          onClick={() => setIsOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            background: "rgba(11, 15, 25, 0.92)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            padding: "20px 24px",
          }}
        >
          {/* Modal Header Controls */}
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%",
              maxWidth: 1040,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 14,
              color: "#ffffff",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <FileText style={{ width: 20, height: 20, color: "var(--accent-primary)" }} />
              <span style={{ fontSize: 17, fontWeight: 700 }}>
                Report Document Preview ({reportCode})
              </span>
              <span
                style={{
                  fontSize: 11,
                  background: "rgba(255,255,255,0.15)",
                  padding: "3px 10px",
                  borderRadius: 12,
                  color: "#e2e8f0",
                }}
              >
                {wasAutoOpened ? "Generated PDF Document" : "Official Surveyor Draft"}
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <a
                href={downloadUrl}
                download={fileName}
                className="btn-primary-modern"
                style={{
                  padding: "8px 16px",
                  fontSize: 12,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <FileDown style={{ width: 14, height: 14 }} />
                <span>Download PDF</span>
              </a>

              <button
                type="button"
                onClick={() => setIsOpen(false)}
                style={{
                  background: "rgba(255,255,255,0.12)",
                  border: "none",
                  color: "#fff",
                  width: 32,
                  height: 32,
                  borderRadius: "50%",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  transition: "background 0.2s",
                }}
                title="Close Modal (ESC)"
              >
                <X style={{ width: 16, height: 16 }} />
              </button>
            </div>
          </div>

          {/* PDF Viewport Frame */}
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "100%",
              maxWidth: 1040,
              height: "82vh",
              borderRadius: 10,
              overflow: "hidden",
              background: "#ffffff",
              boxShadow: "0 25px 60px rgba(0, 0, 0, 0.6)",
              border: "1px solid rgba(255, 255, 255, 0.15)",
            }}
          >
            <iframe
              src={viewUrl}
              title={`PDF Preview ${reportCode}`}
              style={{
                width: "100%",
                height: "100%",
                border: "none",
              }}
            />
          </div>

          <div style={{ marginTop: 10, color: "#94a3b8", fontSize: 12 }}>
            Press <kbd style={{ background: "rgba(255,255,255,0.1)", padding: "2px 6px", borderRadius: 4 }}>ESC</kbd> or click outside to close preview
          </div>
        </div>
      )}
    </>
  );
}
