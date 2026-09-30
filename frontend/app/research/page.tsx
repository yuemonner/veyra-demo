import Link from "next/link";

const directions = [
  ["01", "Human-Machine Decision Systems", "How human judgment and machine cognition combine into one decision."],
  ["02", "Operational Intelligence", "How teams make decisions across complex physical systems under uncertainty."],
  ["03", "Cognitive Architectures", "How memory, prediction, dissent, risk and context combine into coherent decisions."],
];

export default function ResearchPage() {
  return (
    <div className="lab-page">
      <header className="lab-header">
        <Link className="lab-brand" href="/">Silken Reason <span>Independent Research Lab</span></Link>
        <nav>
          <Link href="/research">Research</Link>
          <Link href="/veyra">Veyra</Link>
          <Link href="/conversations">Conversations</Link>
        </nav>
      </header>

      <main>
        <section className="lab-hero compact">
          <span className="eyebrow">Research</span>
          <h1>Questions before products.</h1>
          <p>Silken Reason studies how people, machines and organizations turn uncertain evidence into action.</p>
        </section>

        <section className="research-list">
          {directions.map(([number, title, text]) => (
            <article key={number}>
              <span>{number}</span>
              <div>
                <h2>{title}</h2>
                <p>{text}</p>
              </div>
            </article>
          ))}
        </section>

        <section className="lab-section product-callout">
          <div>
            <span className="eyebrow">Commercial experiment</span>
            <h2>Veyra grows from Operational Intelligence.</h2>
            <p>It turns machine incidents into decision workflows that carry evidence, action and outcome forward.</p>
          </div>
          <Link className="button primary" href="/veyra">Explore Veyra →</Link>
        </section>
      </main>
    </div>
  );
}
