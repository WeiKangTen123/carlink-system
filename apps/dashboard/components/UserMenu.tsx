"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, LogOut, UserCog } from "lucide-react";
import { API_BASE_URL, type CurrentUser } from "@/lib/api";
import { clearEvidenceCache } from "@/lib/evidenceCache";
import { ROLE_LABEL } from "@/lib/signup";

export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return ((parts[0][0] || "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export async function signOut(): Promise<void> {
  try {
    await fetch(`${API_BASE_URL}/auth/logout`, { method: "POST", credentials: "include" });
  } catch {
    // Leave anyway; the session still ends on its own at the idle limit.
  }
  await clearEvidenceCache();
  window.location.assign("/login");
}

export function UserMenu({ user }: { user: CurrentUser }) {
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={ref}>
      <button
        type="button"
        className="user-menu-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="user-avatar">{initialsOf(user.name)}</span>
        <span className="user-menu-name">{user.name}</span>
        <ChevronDown style={{ width: 13, height: 13, color: "var(--text-muted)" }} />
      </button>

      {open && (
        <div className="user-menu-panel" role="menu">
          <div className="user-menu-head">
            <div style={{ fontWeight: 800, fontSize: 13 }}>{user.name}</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", wordBreak: "break-all" }}>
              {user.username ? `${user.username} · ` : ""}
              {user.email}
            </div>
            <span className={`role-chip ${user.role}`}>{ROLE_LABEL[user.role] ?? user.role}</span>
          </div>
          <Link href="/account" className="user-menu-item" role="menuitem" onClick={() => setOpen(false)}>
            <UserCog style={{ width: 14, height: 14 }} /> My account &amp; password
          </Link>
          <button
            type="button"
            className="user-menu-item danger"
            role="menuitem"
            disabled={leaving}
            onClick={() => {
              setLeaving(true);
              signOut();
            }}
          >
            <LogOut style={{ width: 14, height: 14 }} /> {leaving ? "Signing out…" : "Sign out"}
          </button>
        </div>
      )}
    </div>
  );
}
