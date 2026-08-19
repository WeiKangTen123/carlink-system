"use client";

import React from "react";

interface ClaimLifecycleStepperProps {
  status?: string;
  channel?: string;
  aiScore?: string;
  insurerName?: string;
}

export function ClaimLifecycleStepper({
  status = "Pending Review",
  channel = "Telegram",
  aiScore = "97.1%",
  insurerName = "NTUC Income",
}: ClaimLifecycleStepperProps) {
  const isSignedOff = status === "Signed Off" || status === "Approved";

  return (
    <div className="claim-stepper-glass">
      {/* 1. Bot Ingestion */}
      <div className="step-node completed">
        <div className="step-circle">✓</div>
        <div>
          <div className="step-title">1. Bot Ingestion</div>
          <div className="step-desc">{channel} Intake</div>
        </div>
      </div>

      {/* 2. AI Vision Scan */}
      <div className="step-node completed">
        <div className="step-circle">✓</div>
        <div>
          <div className="step-title">2. AI Vision Scan</div>
          <div className="step-desc">Gemini Vision &bull; {aiScore}</div>
        </div>
      </div>

      {/* 3. Surveyor Audit */}
      <div className={`step-node ${isSignedOff ? "completed" : "active"}`}>
        <div className="step-circle">{isSignedOff ? "✓" : "3"}</div>
        <div>
          <div className="step-title">3. Surveyor Audit</div>
          <div className="step-desc">{isSignedOff ? "Audited" : "Under Review"}</div>
        </div>
      </div>

      {/* 4. Manager Sign-Off */}
      <div className={`step-node ${isSignedOff ? "completed" : ""}`}>
        <div className="step-circle">{isSignedOff ? "✓" : "4"}</div>
        <div>
          <div className="step-title">4. Manager Sign-Off</div>
          <div className="step-desc">{isSignedOff ? "Signed & Locked" : "Pending Sign-Off"}</div>
        </div>
      </div>

      {/* 5. Claim Settled */}
      <div className="step-node">
        <div className="step-circle">5</div>
        <div>
          <div className="step-title">5. Claim Settled</div>
          <div className="step-desc">{insurerName || "Insurance Indemnity"}</div>
        </div>
      </div>
    </div>
  );
}
