import Link from "next/link";

export default function ConversationsPage() {
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
          <span className="eyebrow">Conversations</span>
          <h1>How people and machines make sense of uncertain worlds.</h1>
          <p>A future home for interviews, field notes and conversations with builders working on robots, autonomy, cognition and operational systems.</p>
        </section>

        <section className="lab-entry-grid">
          <article className="lab-entry">
            <span className="eyebrow">Format</span>
            <h2>Interviews with builders, researchers and operators.</h2>
          </article>
          <article className="lab-entry">
            <span className="eyebrow">Theme</span>
            <h2>How intelligence becomes action in real environments.</h2>
          </article>
          <article className="lab-entry">
            <span className="eyebrow">Status</span>
            <h2>Coming soon.</h2>
          </article>
        </section>

        <section className="lab-section about-strip">
          <span className="eyebrow">First thread</span>
          <p>Physical AI is leaving the lab. The interesting question is no longer only what machines can do, but how teams understand, trust, correct and remember their actions.</p>
        </section>
      </main>
    </div>
  );
}
