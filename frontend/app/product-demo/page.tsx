"use client";

import Link from "next/link";
import { useState } from "react";
import InvestigationAnalysis from "../../components/InvestigationAnalysis";

const sections = [
  ["overview", "Situation"], ["checks", "Next action"], ["outcomes", "Result & learning"],
];

export default function ProductDemoPage() {
  const [stage, setStage] = useState("overview");
  return <main className="investigation-demo">
    <header className="investigation-topbar">
      <Link href="/veyra" className="ide-brand"><span>V</span> Veyra</Link>
      <span>FC-021 · Safe-stop after autonomy 2.7</span>
      <small>Synthetic case</small>
    </header>
    <div className="investigation-workspace">
      <nav aria-label="Investigation sections">{sections.map(([key, label]) =>
        <button key={key} className={stage === key ? "active" : ""} aria-current={stage === key ? "page" : undefined} onClick={() => setStage(key)}>{label}</button>
      )}<details className="investigation-nav-details"><summary>Technical review</summary>{[["fleet", "Compare machines"], ["hypotheses", "Possible causes"], ["evidence", "Evidence & timeline"]].map(([key, label]) => <button key={key} className={stage === key ? "active" : ""} aria-current={stage === key ? "page" : undefined} onClick={() => setStage(key)}>{label}</button>)}</details></nav>
      <div className="investigation-content">
        <InvestigationAnalysis stage={stage} onNavigate={setStage} />
      </div>
    </div>
    <footer className="investigation-footer">Evidence-led decisions · Engineer-reviewed actions · Reusable learning</footer>
  </main>;
}
