"use client";

import { useEffect, useState } from "react";

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
        background: "var(--surface-hover)",
        border: "1px solid var(--border-color)",
        padding: "3px",
        borderRadius: "24px",
        gap: "3px",
      }}
      title="Toggle Dark / White Theme"
    >
      <button
        type="button"
        onClick={() => setThemeMode("dark")}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "5px",
          fontSize: "11px",
          fontWeight: 700,
          padding: "4px 10px",
          borderRadius: "18px",
          border: "none",
          background: theme === "dark" ? "linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)" : "transparent",
          color: theme === "dark" ? "#ffffff" : "var(--text-muted)",
          cursor: "pointer",
          boxShadow: theme === "dark" ? "0 0 10px rgba(56, 189, 248, 0.35)" : "none",
          transition: "all 0.2s ease",
        }}
      >
        <span>🌙</span> Dark
      </button>

      <button
        type="button"
        onClick={() => setThemeMode("light")}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "5px",
          fontSize: "11px",
          fontWeight: 700,
          padding: "4px 10px",
          borderRadius: "18px",
          border: "none",
          background: theme === "light" ? "#ffffff" : "transparent",
          color: theme === "light" ? "#0f172a" : "var(--text-muted)",
          cursor: "pointer",
          boxShadow: theme === "light" ? "0 2px 8px rgba(0,0,0,0.15)" : "none",
          transition: "all 0.2s ease",
        }}
      >
        <span>☀️</span> White
      </button>
    </div>
  );
}
