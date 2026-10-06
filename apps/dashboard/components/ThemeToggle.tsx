"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

export function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">("dark");

  useEffect(() => {
    const saved = localStorage.getItem("carlink-theme") as "light" | "dark" | null;
    const initial = saved || "dark";
    setTheme(initial);
    document.documentElement.setAttribute("data-theme", initial);
  }, []);

  const setThemeMode = (mode: "light" | "dark") => {
    setTheme(mode);
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("carlink-theme", mode);
  };

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        background: "var(--surface-elevated)",
        border: "1px solid var(--border-color)",
        padding: "3px",
        borderRadius: "20px",
        gap: "2px",
      }}
      title="Toggle Dark / Light Theme"
    >
      <button
        type="button"
        onClick={() => setThemeMode("dark")}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "5px",
          fontSize: "11px",
          fontWeight: 600,
          padding: "4px 9px",
          borderRadius: "16px",
          border: "none",
          background: theme === "dark" ? "var(--accent-primary)" : "transparent",
          color: theme === "dark" ? "#ffffff" : "var(--text-muted)",
          cursor: "pointer",
          boxShadow: theme === "dark" ? "0 2px 8px var(--accent-glow)" : "none",
          transition: "all 0.2s ease",
        }}
      >
        <Moon style={{ width: 13, height: 13 }} />
        <span>Dark</span>
      </button>

      <button
        type="button"
        onClick={() => setThemeMode("light")}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "5px",
          fontSize: "11px",
          fontWeight: 600,
          padding: "4px 9px",
          borderRadius: "16px",
          border: "none",
          background: theme === "light" ? "#ffffff" : "transparent",
          color: theme === "light" ? "#0f172a" : "var(--text-muted)",
          cursor: "pointer",
          boxShadow: theme === "light" ? "0 2px 6px rgba(0,0,0,0.12)" : "none",
          transition: "all 0.2s ease",
        }}
      >
        <Sun style={{ width: 13, height: 13 }} />
        <span>Light</span>
      </button>
    </div>
  );
}

