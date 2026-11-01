"use client";

import { useState } from "react";
import { Check, Copy, MessageCircle, Phone } from "lucide-react";
import { phoneLinks } from "@/lib/api";

/** [Call] [WhatsApp] [Copy] for a sender's phone (release P6). Only for a
 * plain +digits number; WhatsApp isn't offered for a Singapore landline. */
export function PhoneActions({ phone, kind, compact = false }: { phone: string; kind?: string | null; compact?: boolean }) {
  const links = phoneLinks(phone, kind);
  const [copied, setCopied] = useState(false);
  if (!links) return null;

  const copy = () => {
    navigator.clipboard
      ?.writeText(phone.replace(/[\s-]/g, ""))
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => {
        // Clipboard refused (an old browser, or not allowed): nothing to do.
      });
  };

  return (
    <span className={`inbox-phone-actions ${compact ? "compact" : ""}`}>
      <a href={links.tel} className="btn-secondary-modern inbox-phone-btn">
        <Phone style={{ width: 12, height: 12 }} /> Call
      </a>
      {links.whatsapp && (
        <a href={links.whatsapp} target="_blank" rel="noopener noreferrer" className="btn-secondary-modern inbox-phone-btn">
          <MessageCircle style={{ width: 12, height: 12 }} /> WhatsApp
        </a>
      )}
      {!compact && (
        <button type="button" className="btn-secondary-modern inbox-phone-btn" onClick={copy}>
          {copied ? <Check style={{ width: 12, height: 12 }} /> : <Copy style={{ width: 12, height: 12 }} />} {copied ? "Copied" : "Copy"}
        </button>
      )}
    </span>
  );
}
