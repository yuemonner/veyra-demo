import Link from "next/link";
import { getJson } from "../lib/api";

type Investigation = { id: string; asset_id: string; title: string; status: string };
type Asset = { id: string; name: string; asset_type: string; site: string };

export default async function Page() {
  let assets: Asset[] = [];
  let investigations: Investigation[] = [];
  try {
    assets = await getJson<Asset[]>("/assets");
    investigations = await getJson<Investigation[]>("/investigations");
  } catch {
    // The UI still renders before the backend is started.
  }
  const active = investigations[0];
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="mark">V</span> Veyra</div>
        <nav className="nav"><Link className="active" href="/">Overview</Link><Link href="/product-demo">Live product</Link><Link href="/cinematic">Cinematic story</Link></nav>
        <div className="boundary">Read-only beside your existing stack. Machine control remains in customer systems.</div>
      </aside>
      <main className="main">
        <div className="topbar"><span className="eyebrow">Physical AI Operations</span><div className="demo-controls"><Link className="button lime" href="/cinematic">Review cinematic case</Link><Link className="button" href="/product-demo">Open live product</Link></div></div>
        <section className="hero">
          <span className="eyebrow">Operational intelligence for Physical AI.</span>
          <h1>One update. Six machines. Two behave differently.</h1>
          <p>Veyra reconstructs what changed, shows where else the same conditions exist, and follows the case through action and outcome.</p>
          <p><b>See what changed. Decide what to do. Know whether it worked. Reuse it next time.</b></p>
          <div className="hero-actions"><Link className="button primary" href="/product-demo">Open live product</Link><Link className="button" href="/cinematic">Review cinematic case</Link></div>
        </section>
        <div className="grid three">
          <article className="panel metric"><span>Test set</span><strong>{assets.length || "6"}</strong><p>robots updated.</p></article>
          <article className="panel metric"><span>Affected</span><strong>2</strong><p>grip pose drift.</p></article>
          <article className="panel metric"><span>Stable</span><strong>4</strong><p>same policy, no signal.</p></article>
        </div>
        <section className="investigation panel">
          <span className="eyebrow">Backend v0 live</span>
          <h2>Product core behind the demo</h2>
          <p>The cinematic story and live investigation run on the same backend: evidence ingestion, canonical evidence model, reconstructing what changed, finding where else the pattern appears, comparing affected and healthy machines, deterministic Decision Package generation, action recording, outcome linking and similar-case retrieval.</p>
          <div className="backend-grid">
            <b>Evidence → Decision state → Action → Outcome → Learning</b>
            <b>source evidence ingestion</b>
            <b>canonical evidence model</b>
            <b>event_time / known_at / ingested_at</b>
            <b>reconstruct what changed around a case</b>
            <b>find where else the same pattern appears</b>
            <b>compare affected vs healthy machines</b>
            <b>decision state</b>
            <b>team action record</b>
            <b>outcome linking</b>
            <b>outcome-linked precedent</b>
          </div>
        </section>
        <section className="investigation panel">
          <span className="eyebrow">Current investigation</span>
          <h2>{active?.asset_id || "AMR-001"} · {active?.title || "Start the backend and reset demo data"}</h2>
          <p>Operational Case: evidence, decision state, action, outcome and reusable learning.</p>
          <Link className="button primary" href="/product-demo">Open live product</Link>
        </section>
      </main>
    </div>
  );
}
