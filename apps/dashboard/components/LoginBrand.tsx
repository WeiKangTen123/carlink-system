import { CarFront } from "lucide-react";

/** The logo at the top of the public cards: sign-in, request access,
 * invite links and the privacy notice. */
export function LoginBrand() {
  return (
    <div className="login-brand">
      <div className="brand-icon">
        <CarFront style={{ width: 18, height: 18 }} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
        <span style={{ fontWeight: 800, letterSpacing: "0.04em", fontSize: "14px" }}>CARLINK</span>
        <span style={{ fontSize: "10px", color: "var(--accent-primary)", fontWeight: 700, letterSpacing: "0.08em" }}>STUDIO 2.0</span>
      </div>
    </div>
  );
}
