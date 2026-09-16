"use client";

import Link from "next/link";
import { useState } from "react";
import { postJson } from "../../lib/api";

export default function DemoControl() {
  const [message, setMessage] = useState("Ready.");
  async function reset() {
    const result = await postJson<{ investigation_id: string }>("/demo/reset");
    setMessage(`Demo reset. Investigation ${result.investigation_id} is ready.`);
  }
  async function lateEvidence() {
    await postJson("/demo/late-evidence");
    setMessage("Outcome update injected: R06 now shows grip pose drift with an earlier event_time.");
  }
  return <div className="shell control-page"><aside className="sidebar"><div className="brand"><span className="mark">V</span> Veyra</div><nav className="nav"><Link href="/">Overview</Link><Link href="/cinematic">Cinematic Demo</Link><Link href="/product-demo">Live product</Link><a className="active">Demo Control</a></nav><div className="boundary">Every control sends a real backend event.</div></aside><main className="main"><section className="hero"><span className="eyebrow">Presenter controls</span><h1>Drive the Operational Case demo from real backend state.</h1><p>Reset the scenario, open the cinematic walkthrough, build the case, inject the later R06 update, then link the outcome into reusable memory.</p></section><div className="demo-controls"><button className="button primary" onClick={reset}>Reset 6-machine scenario</button><button className="button" onClick={lateEvidence}>Inject outcome update</button><Link className="button lime" href="/cinematic">Open Cinematic Demo</Link><Link className="button" href="/product-demo">Open live product</Link></div><p className="footer-note">{message}</p></main></div>;
}
