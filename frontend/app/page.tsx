import Link from "next/link";

const themes = [
  ["Distributed Intelligence", "human + machine + organization"],
  ["Decision Systems", "how action emerges under uncertainty"],
  ["Physical Intelligence", "robots, embodied systems, fleet operations"],
  ["Memory & Adaptation", "how systems learn from prior outcomes"],
];

const questions = [
  "Where does a decision live when both a human and a machine contribute to it?",
  "What should an autonomous system know about what it does not know?",
  "How does operational knowledge survive when the people, machines and models keep changing?",
  "What new institutions emerge when machines begin making consequential decisions?",
];

export default function SilkenReasonHome() {
  return (
    <div className="lab-page">
      <header className="lab-header">
        <Link className="lab-brand" href="/">Silken Reason <span>Independent Research Lab</span></Link>
        <nav>
          <Link href="/research">Research</Link>
          <a href="#products">Products</a>
          <Link href="/conversations">Conversations</Link>
          <a href="#about">About</a>
        </nav>
      </header>

      <main>
        <section className="lab-hero">
          <span className="eyebrow">Silken Reason Lab</span>
          <h1>Silken Reason</h1>
          <p>Silken Reason is an independent research lab studying intelligence in real-world systems.</p>
          <div className="lab-theme-grid">
            {themes.map(([title, text]) => (
              <article key={title}>
                <span>{title}</span>
                <p>{text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="lab-entry-grid" aria-label="Primary areas">
          <Link href="/research" className="lab-entry">
            <span className="eyebrow">Research</span>
            <h2>Human-machine cognition, distributed intelligence, memory, adaptation and real-world systems.</h2>
          </Link>
          <a href="#products" className="lab-entry">
            <span className="eyebrow">Products</span>
            <h2>What we build from the questions we pursue.</h2>
          </a>
          <Link href="/conversations" className="lab-entry">
            <span className="eyebrow">Conversations</span>
            <h2>How people and machines think, act and make sense of uncertain worlds.</h2>
          </Link>
        </section>

        <section className="lab-section">
          <span className="eyebrow">What we are interested in</span>
          <div className="question-list">
            {questions.map((question) => <p key={question}>{question}</p>)}
          </div>
        </section>

        <section id="products" className="lab-section product-callout">
          <div>
            <span className="eyebrow">First product</span>
            <h2>Veyra</h2>
            <p>Operational Intelligence for Physical AI.</p>
            <p>Veyra helps teams understand what changed, decide what to do and learn from what worked across deployed physical systems.</p>
          </div>
          <Link className="button primary" href="/veyra">Explore Veyra →</Link>
        </section>

        <section id="about" className="lab-section about-strip">
          <span className="eyebrow">About</span>
          <p>Silken Reason is a small independent lab. We use research, product experiments and conversations to study how intelligence becomes action in the physical world.</p>
        </section>
      </main>
    </div>
  );
}
