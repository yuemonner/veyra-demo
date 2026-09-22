import Link from "next/link";

const machines = Array.from({ length: 12 }, (_, index) => index + 1);
const affected = new Set([3, 5, 8]);
const watch = new Set([11]);

const compareRows = [
  ["Autonomy release 2.7", "3/3", "9/9"],
  ["Localization profile L4", "3/3", "2/9"],
  ["LiDAR firmware 5.3", "3/3", "4/9"],
  ["Map M19", "3/3", "5/9"],
  ["Loading zone B", "3/3", "1/9"],
];

const options = [
  ["Monitor", "Low cost", "Affected machines may keep entering safe-stop"],
  ["Remote recovery", "No field visit", "Cause remains unresolved"],
  ["Rollback release", "Interrupts rollout", "Autonomy 2.7 also runs on healthy machines"],
  ["Dispatch technician", "High cost", "Current evidence does not yet support it"],
];

export default function ProductPage() {
  return (
    <main className="product-page">
      <section className="product-page-hero">
        <nav>
          <Link className="workspace-brand" href="/"><span className="mark">V</span> Veyra</Link>
          <Link className="button" href="/product-demo">Open Product Demo</Link>
        </nav>
        <div className="product-page-title">
          <span className="eyebrow">Product page</span>
          <h1>Operational case workspace for Physical AI.</h1>
          <p>One machine group changes behavior. Veyra shows what changed, where else it appears, what the team can do, what they chose and what happened after.</p>
        </div>
        <div className="case-pills product-page-pills">
          <b className="pill-alert">3 affected</b>
          <b className="pill-blue">9 healthy</b>
          <b>12 updated</b>
          <b>action pending</b>
        </div>
      </section>

      <section className="product-page-workspace">
        <aside className="product-machine-panel">
          <div className="rail-title"><span className="eyebrow">Machines</span><b>12</b></div>
          <div className="machine-grid">
            {machines.map((id) => <span key={id} className={affected.has(id) ? "affected" : watch.has(id) ? "watch" : "healthy"} />)}
          </div>
          <dl className="case-facts">
            <div><dt>affected</dt><dd>EX03, EX05, EX08</dd></div>
            <div><dt>watch</dt><dd>EX11</dd></div>
            <div><dt>release</dt><dd>2.7</dd></div>
            <div><dt>profile</dt><dd>L4 + zone B</dd></div>
          </dl>
          <div className="rail-card">
            <span className="eyebrow">Current question</span>
            <p>Where else is this happening?</p>
          </div>
        </aside>

        <div className="product-case-stack">
          <ProductSection number="01" title="Overview" heading="Three excavators entered safe-stop after the same deployment.">
            <p>Twelve remote-operated machines received autonomy release 2.7. EX03, EX05 and EX08 entered safe-stop near loading zone B. EX11 shares the exposure but had no known issue at decision time.</p>
            <div className="product-metrics">
              <MiniMetric label="14:02" value="Release 2.7" note="deployment started" />
              <MiniMetric label="14:11" value="First safe-stop" note="EX03 signal" />
              <MiniMetric label="14:26" value="3 affected" note="pattern detected" />
              <MiniMetric label="14:31" value="Review opened" note="operator review" />
            </div>
          </ProductSection>

          <ProductSection number="02" title="Changes" heading="What changed before the failures?">
            <div className="product-cards">
              <MiniCard title="Autonomy stack" value="2.6 to 2.7" />
              <MiniCard title="Localization config" value="L3 to L4" />
              <MiniCard title="LiDAR firmware" value="5.2 to 5.3" />
              <MiniCard title="Map version" value="M18 to M19" />
              <MiniCard title="Machine state" value="safe-stop near loading zone B" />
              <MiniCard title="Human context" value="operator review at 14:31" />
            </div>
            <p className="section-note">Multiple changes happened before the first known safe-stop. Cause is not proven yet.</p>
          </ProductSection>

          <ProductSection number="03" title="Fleet" heading="Where else does this behavior appear?">
            <table className="table focus-table product-table">
              <thead><tr><th>Context</th><th>Safe-stop</th><th>Healthy</th></tr></thead>
              <tbody>{compareRows.map((row) => <tr key={row[0]}><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td></tr>)}</tbody>
            </table>
            <p className="section-note">Autonomy 2.7 is on all machines. Localization L4 and loading zone B help focus the search. They do not prove the cause.</p>
          </ProductSection>

          <ProductSection number="04" title="Decision" heading="What should the team do now?">
            <div className="product-options">
              {options.map(([name, cost, risk]) => (
                <article key={name}>
                  <span className="eyebrow">Option</span>
                  <b>{name}</b>
                  <p>{cost}</p>
                  <small>{risk}</small>
                </article>
              ))}
            </div>
            <div className="decision-snapshot">
              <div><span className="eyebrow">Known at 14:27</span><b>EX03, EX05 and EX08 affected. EX11 had no known issue.</b></div>
              <div><span className="eyebrow">Still missing</span><b>Planner fallback reason, perception trace and whether remote recovery will hold.</b></div>
            </div>
          </ProductSection>

          <ProductSection number="05" title="Action" heading="What the team chose.">
            <div className="product-cards">
              <MiniCard title="Decision" value="Remote recovery on affected machines" />
              <MiniCard title="Scope" value="EX03, EX05 and EX08" />
              <MiniCard title="Held" value="field dispatch and exposed rollout group" />
              <MiniCard title="Watch" value="EX11" />
              <MiniCard title="Owner" value="Robotics Engineering" />
              <MiniCard title="Record" value="decision-time snapshot sealed" />
            </div>
          </ProductSection>

          <ProductSection number="06" title="Outcome" heading="What happened after the action?">
            <div className="product-cards">
              <MiniCard title="Recovery" value="machines returned to service" />
              <MiniCard title="Dispatch" value="field visit avoided" />
              <MiniCard title="Rollout" value="held for 43 minutes" />
              <MiniCard title="Recurrence" value="EX05 safe-stop six hours later" />
              <MiniCard title="Late evidence" value="EX11 event_time 14:09, known_at 14:31" />
              <MiniCard title="Cause" value="seen after action, not proven yet" />
            </div>
            <p className="section-note">New evidence updates the current case. It does not rewrite what the team knew when it made the decision.</p>
          </ProductSection>

          <ProductSection number="07" title="History" heading="The next case does not start from zero.">
            <div className="precedent-strip">
              <div><span className="eyebrow">Previous conditions</span><b>Autonomy 2.7 · Localization L4 · Loading zone B</b></div>
              <div><span className="eyebrow">Previous action</span><b>Remote recovery · rollout held · field dispatch held</b></div>
              <div><span className="eyebrow">Observed outcome</span><b>Returned to service · no field visit · later recurrence</b></div>
            </div>
            <p className="section-note">When a similar safe-stop pattern appears again, Veyra brings back what was known, what the team did and what happened after.</p>
          </ProductSection>
        </div>
      </section>
    </main>
  );
}

function ProductSection({ number, title, heading, children }: { number: string; title: string; heading: string; children: React.ReactNode }) {
  return (
    <section className="product-section">
      <div className="section-index"><span>{number}</span><b>{title}</b></div>
      <div className="section-body">
        <h2>{heading}</h2>
        {children}
      </div>
    </section>
  );
}

function MiniMetric({ label, value, note }: { label: string; value: string; note: string }) {
  return <article><span>{label}</span><b>{value}</b><small>{note}</small></article>;
}

function MiniCard({ title, value }: { title: string; value: string }) {
  return <article><span className="eyebrow">{title}</span><b>{value}</b></article>;
}
