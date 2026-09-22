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
        <nav className="nav"><Link className="active" href="/">Overview</Link><Link href="/product-demo">Product Demo</Link><Link href="/cinematic">Field Case Replay</Link></nav>
        <div className="boundary">Read-only beside your existing stack. Machine control remains in customer systems.</div>
      </aside>
      <main className="main">
        <div className="topbar"><span className="eyebrow">Physical AI Operations</span><div className="demo-controls"><Link className="button lime" href="/cinematic">Review field case</Link><Link className="button" href="/product-demo">Open Product Demo</Link></div></div>
        <section className="hero">
          <span className="eyebrow">Operational intelligence for Physical AI.</span>
          <h1>One deployment. Twelve machines. Three enter safe-stop.</h1>
          <p>Veyra shows what changed, where else it is happening, what the team can do, and what happened after.</p>
          <p><b>See what changed. Decide what to do. Learn what happened after. Reuse it next time.</b></p>
          <div className="hero-actions"><Link className="button primary" href="/product-demo">Open Product Demo</Link><Link className="button" href="/cinematic">Review field case</Link></div>
        </section>
        <div className="grid three">
          <article className="panel metric"><span>Machine group</span><strong>{assets.length || "12"}</strong><p>machines updated.</p></article>
          <article className="panel metric"><span>Affected</span><strong>3</strong><p>safe-stop near loading zone B.</p></article>
          <article className="panel metric"><span>Stable</span><strong>9</strong><p>same release, no signal.</p></article>
        </div>
        <section className="investigation panel">
          <span className="eyebrow">Backend-backed demo</span>
          <h2>Product core behind the demo</h2>
          <p>The field case replay and Product Demo run on the same backend: source evidence, time-aware records, what changed, where else, affected versus healthy machines, option comparison, action recording, outcome linking and similar-case history.</p>
          <div className="backend-grid">
            <b>Evidence → Decision state → Action → Outcome → Learning</b>
            <b>source evidence ingestion</b>
            <b>shared evidence format</b>
            <b>event_time / known_at / ingested_at</b>
            <b>reconstruct what changed around a case</b>
            <b>find where else the same pattern appears</b>
            <b>compare affected vs healthy machines</b>
            <b>decision state</b>
            <b>team action record</b>
            <b>outcome linking</b>
            <b>outcome-linked history</b>
          </div>
        </section>
        <section className="investigation panel">
          <span className="eyebrow">Current investigation</span>
          <h2>{active?.asset_id || "EX03"} · {active?.title || "Start the backend and reset demo data"}</h2>
          <p>Product Demo: what changed, where else, options, action, outcome and reusable history.</p>
          <Link className="button primary" href="/product-demo">Open Product Demo</Link>
        </section>
      </main>
    </div>
  );
}
