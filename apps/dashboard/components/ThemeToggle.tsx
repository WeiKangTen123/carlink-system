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

  const toggleTheme = () => {
    const nextTheme = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    document.documentElement.setAttribute("data-theme", nextTheme);
    localStorage.setItem("carlink-theme", nextTheme);
  };

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title="Toggle Light / Dark Theme"
      style={{
        background: "var(--surface-hover)",
        border: "1px solid var(--border-color)",
        color: "var(--text-primary)",
        padding: "6px 12px",
        borderRadius: "6px",
        cursor: "pointer",
        fontSize: "13px",
        fontWeight: 600,
        display: "flex",
        alignItems: "center",
        gap: "6px",
        transition: "all 0.15s ease",
      }}
    >
      {theme === "dark" ? "☀️ Light Mode" : "🌙 Dark Mode"}
    </button>
  );
}
