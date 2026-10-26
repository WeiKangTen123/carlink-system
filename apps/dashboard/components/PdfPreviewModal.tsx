"use client";

import { useState, useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import { AlertTriangle, FileText, FileDown, X } from "lucide-react";
import { pdfDownloadUrl, API_BASE_URL, ApiError, apiError, apiFetch } from "@/lib/api";

/** A PDF fetched from /download: on its way, ready (an object URL), or
 * refused. */
type FetchedPdf = { state: "loading" } | { state: "ready"; url: string } | { state: "failed"; message: string };

export function PdfPreviewModal({
  reportId,
  pdfUrl,
  reportCode,
  autoOpen = false,
  isOpen: controlledIsOpen,
  onClose: controlledOnClose,
  trigger,
}: {
  reportId: string;
  pdfUrl?: string | null;
  reportCode: string;
  autoOpen?: boolean;
  isOpen?: boolean;
  onClose?: () => void;
  trigger?: React.ReactNode;
}) {
  const [internalIsOpen, setInternalIsOpen] = useState(autoOpen);
  const [wasAutoOpened] = useState(autoOpen);
  const router = useRouter();
  const pathname = usePathname();

  const isControlled = controlledIsOpen !== undefined;
  const isOpen = isControlled ? controlledIsOpen : internalIsOpen;
  const handleClose = () => {
    if (isControlled) {
      controlledOnClose?.();
    } else {
      setInternalIsOpen(false);
    }
  };

  useEffect(() => {
    if (autoOpen) {
      router.replace(pathname);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    if (isOpen) window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  // No stored PDF yet: /download makes one first, and can refuse
  // (render_limit, render_busy). Fetched here rather than loaded straight
  // into the iframe, which would show the refusal as raw JSON.
  const fetchesPdf = isOpen && !pdfUrl;
  const [fetched, setFetched] = useState<FetchedPdf>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!fetchesPdf) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    (async () => {
      try {
        const res = await apiFetch(pdfDownloadUrl(reportId), { cache: "no-store", signal: controller.signal });
        if (!res.ok) throw await apiError(res, "Couldn't make the PDF");
        const blob = await res.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob.type === "application/pdf" ? blob : new Blob([blob], { type: "application/pdf" }));
        setFetched({ state: "ready", url: objectUrl });
      } catch (err) {
        if (controller.signal.aborted) return;
        setFetched({
          state: "failed",
          message: err instanceof ApiError ? err.message : "Couldn't reach the server. Check the connection and try again.",
        });
      }
    })();
    // Closed, another case, or Try again: drop the request and the PDF held
    // in memory, and start the next open from "loading" rather than from a
    // URL that no longer works.
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setFetched({ state: "loading" });
    };
  }, [fetchesPdf, reportId, attempt]);

  const fetchedUrl = !pdfUrl && fetched.state === "ready" ? fetched.url : null;
  const viewUrl = pdfUrl ? (pdfUrl.startsWith("http") ? pdfUrl : `${API_BASE_URL}${pdfUrl}`) : fetchedUrl;
  // The PDF already fetched for the preview, so Download doesn't ask again.
  const downloadUrl = fetchedUrl ?? pdfDownloadUrl(reportId);
  const fileName = `Car_Incident_Report_${reportCode}.pdf`;

  return (
    <>
      {trigger !== null && (
        trigger !== undefined ? (
          <span onClick={() => isControlled ? undefined : setInternalIsOpen(true)}>
            {trigger}
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setInternalIsOpen(true)}
            className="btn-secondary-modern"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              cursor: "pointer",
            }}
          >
            <FileText style={{ width: 14, height: 14, color: "var(--accent-primary)" }} />
            <span>Preview & Download PDF</span>
          </button>
        )
      )}

      {isOpen && (
        <div
          onClick={handleClose}
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
                onClick={handleClose}
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
              background: viewUrl ? "#ffffff" : "rgba(255, 255, 255, 0.04)",
              boxShadow: "0 25px 60px rgba(0, 0, 0, 0.6)",
              border: "1px solid rgba(255, 255, 255, 0.15)",
            }}
          >
            {viewUrl ? (
              <iframe
                src={viewUrl}
                title={`PDF Preview ${reportCode}`}
                style={{
                  width: "100%",
                  height: "100%",
                  border: "none",
                }}
              />
            ) : (
              <div
                role={fetched.state === "failed" ? "alert" : "status"}
                style={{
                  height: "100%",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 12,
                  padding: 24,
                  textAlign: "center",
                  color: "#e2e8f0",
                  fontSize: 14,
                }}
              >
                {fetched.state === "failed" ? (
                  <>
                    <AlertTriangle style={{ width: 28, height: 28, color: "var(--badge-amber-text)" }} />
                    <div style={{ fontWeight: 700, fontSize: 15 }}>Couldn&apos;t show the PDF</div>
                    <div style={{ maxWidth: 440, lineHeight: 1.5, color: "#94a3b8" }}>{fetched.message}</div>
                    <button
                      type="button"
                      onClick={() => setAttempt((n) => n + 1)}
                      style={{
                        background: "rgba(255,255,255,0.12)",
                        border: "none",
                        color: "#fff",
                        padding: "8px 16px",
                        borderRadius: 8,
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: "pointer",
                      }}
                    >
                      Try again
                    </button>
                  </>
                ) : (
                  <span style={{ color: "#94a3b8" }}>Loading the PDF…</span>
                )}
              </div>
            )}
          </div>

          <div style={{ marginTop: 10, color: "#94a3b8", fontSize: 12 }}>
            Press <kbd style={{ background: "rgba(255,255,255,0.1)", padding: "2px 6px", borderRadius: 4 }}>ESC</kbd> or click outside to close preview
          </div>
        </div>
      )}
    </>
  );
}
