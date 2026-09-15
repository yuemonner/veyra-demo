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
        <nav className="nav"><Link className="active" href="/">Overview</Link><Link href={active ? `/investigations/${active.id}` : "/"}>Investigations</Link><Link href="/demo-control">Demo Control</Link></nav>
        <div className="boundary">We do not touch your robots, code or stack. Veyra turns machine evidence into decision records.</div>
      </aside>
      <main className="main">
        <div className="topbar"><span className="eyebrow">Physical AI Operations</span><div className="demo-controls"><Link className="button lime" href="/cinematic">Cinematic demo</Link><Link className="button" href="/demo-control">Presenter controls</Link></div></div>
        <section className="hero">
          <span className="eyebrow">Decision infrastructure for Physical AI.</span>
          <h1>6 robots. One model update. 90 seconds.</h1>
          <p>Six robots run the same manipulation policy. Two start behaving differently.</p>
          <p><b>Veyra reconstructs what changed, what the team knew, who approved action, and what happened next.</b></p>
          <div className="hero-actions"><Link className="button primary" href="/cinematic">Start cinematic demo</Link><Link className="button" href={active ? `/investigations/${active.id}` : "/demo-control"}>{active ? "Open live system" : "Initialize demo"}</Link></div>
        </section>
        <div className="grid three">
          <article className="panel metric"><span>Test set</span><strong>{assets.length || "6"}</strong><p>robots updated.</p></article>
          <article className="panel metric"><span>Affected</span><strong>2</strong><p>grip pose drift.</p></article>
          <article className="panel metric"><span>Stable</span><strong>4</strong><p>same policy, no signal.</p></article>
        </div>
        <section className="investigation panel">
          <span className="eyebrow">Backend v0 live</span>
          <h2>Product core behind the demo</h2>
          <p>The cinematic story and live investigation run on the same backend: evidence ingestion, canonical evidence model, reconstructing what changed before an incident, finding where else the same pattern appears, comparing affected and healthy machines, deterministic Decision Package generation, sealed decision-time snapshots, human identity and signature, standalone verification, outcome linking and Decision Precedent.</p>
          <div className="backend-grid">
            <b>Evidence → Belief → Decision → Action → Outcome</b>
            <b>source evidence ingestion</b>
            <b>canonical evidence model</b>
            <b>event_time / known_at / ingested_at</b>
            <b>reconstruct what changed before an incident</b>
            <b>find where else the same pattern appears</b>
            <b>compare affected vs healthy machines</b>
            <b>sealed Decision Package + hash</b>
            <b>human identity + signature</b>
            <b>standalone verification</b>
            <b>outcome-linked precedent</b>
          </div>
        </section>
        <section className="investigation panel">
          <span className="eyebrow">Strategic wedge</span>
          <h2>Copy the proven market motion. Own the next abstraction.</h2>
          <p>Veyra starts with the workflow buyers already understand: incident reconstruction, historical comparison, support and engineering review. Then it carries the record forward into decision, action and outcome.</p>
          <div className="backend-grid">
            <b>Validated workflow: connected smart crane</b>
            <b>Not robotics-native only</b>
            <b>Industrial machines</b>
            <b>Robot cells</b>
            <b>Autonomous systems</b>
            <b>Smart equipment</b>
            <b>Production systems</b>
          </div>
        </section>
        <section className="investigation panel">
          <span className="eyebrow">Current investigation</span>
          <h2>{active?.asset_id || "AMR-001"} · {active?.title || "Start the backend and reset demo data"}</h2>
          <p>Evidence → Belief → Decision → Action → Outcome.</p>
          <Link className="button primary" href={active ? `/investigations/${active.id}` : "/demo-control"}>{active ? "Open Investigation" : "Initialize Demo"}</Link>
        </section>
      </main>
    </div>
  );
}
