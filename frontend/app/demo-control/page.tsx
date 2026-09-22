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
    setMessage("Outcome update injected: EX11 now shows safe-stop behavior with an earlier event_time.");
  }
  return <div className="shell control-page"><aside className="sidebar"><div className="brand"><span className="mark">V</span> Veyra</div><nav className="nav"><Link href="/">Overview</Link><Link href="/cinematic">Field Case Replay</Link><Link href="/product-demo">Product Demo</Link><a className="active">Demo Control</a></nav><div className="boundary">Every control sends a real backend event.</div></aside><main className="main"><section className="hero"><span className="eyebrow">Presenter controls</span><h1>Drive the remote equipment demo from real backend state.</h1><p>Reset the scenario, open the field case replay, compare options, inject the later EX11 update, then link the outcome so the next case can reuse it.</p></section><div className="demo-controls"><button className="button primary" onClick={reset}>Reset 12-machine scenario</button><button className="button" onClick={lateEvidence}>Inject outcome update</button><Link className="button lime" href="/cinematic">Open Field Case Replay</Link><Link className="button" href="/product-demo">Open Product Demo</Link></div><p className="footer-note">{message}</p></main></div>;
}
