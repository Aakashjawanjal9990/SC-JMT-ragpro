import { Link } from 'react-router-dom';
import {
  BookMarked,
  Gavel,
  GitCompare,
  GraduationCap,
  MessageSquareText,
  Microscope,
  Repeat2,
  ShieldAlert,
} from 'lucide-react';
import Logo from '../components/Logo';
import { PrimaryButton, SecondaryButton } from '../components/Buttons';
import scales from '../assets/scales.png';

const FEATURES = [
  [
    MessageSquareText,
    'Ask in your own words',
    'Begin with a legal issue, a principle or a judgment. No search syntax needed.',
  ],
  [
    BookMarked,
    'Keep authority close',
    'Read each explanation beside its judgment citation and source context.',
  ],
  [
    Repeat2,
    'Follow the next question',
    'Clarify a point, ask for an overview, and revisit earlier research.',
  ],
  [
    GitCompare,
    'Compare cases',
    'Set two judgments side by side and see where the reasoning agrees or departs.',
  ],
];

const STEPS = [
  [
    'Frame your question',
    'Sign in and describe the issue or principle you want to understand.',
  ],
  [
    'Read a grounded answer',
    'Relevant judgment passages shape the answer, with the citation shown.',
  ],
  [
    'Verify, then go deeper',
    'Open the full judgment, check its context, and ask a sharper follow-up.',
  ],
];

const AUDIENCE = [
  [
    Gavel,
    'Advocates',
    'Orient yourself on an issue before a closer reading of the judgment.',
  ],
  [
    GraduationCap,
    'Law students',
    'Go from a case name to its holding and constitutional context.',
  ],
  [
    Microscope,
    'Researchers',
    'Use follow-ups to clarify concepts and decide what to verify next.',
  ],
];

const FAQ = [
  [
    'Do I need an account to ask a question?',
    'Yes. Sign in or create an account to submit a research question.',
  ],
  [
    'What does “grounded in judgments” mean?',
    'Answers are built from retrieved Supreme Court judgment passages, so you can read the explanation beside its source. It does not guarantee accuracy.',
  ],
  [
    'Can I treat an answer as legal advice?',
    'No. Answers are research assistance. Check the original judgment and consult a qualified professional for your matter.',
  ],
];

export default function Landing() {
  return (
    <div className="lp">
      <header className="lp-head">
        <Logo />

        <nav className="lp-nav">
          <a href="#features">The workspace</a>
          <a href="#how">How it works</a>
        </nav>

        <div className="row">
          <SecondaryButton as={Link} to="/register" userIcon>
            Create Account
          </SecondaryButton>

          <PrimaryButton as={Link} to="/login">
            Sign In
          </PrimaryButton>
        </div>
      </header>

      <section className="lp-hero">
        <img className="watermark" src={scales} alt="" />

        <div className="lp-copy">
          <p className="tag">The law, with a clearer line of sight.</p>

          <h1>
            From legal question <span>to grounded insight.</span>
          </h1>

          <p className="lead">
            Research Supreme Court judgments with AI answers that keep the
            source in view. Study a case, compare several, and verify every
            claim against the original.
          </p>

          <div className="cta">
            <div className="row">
              <PrimaryButton as={Link} to="/login">
                Sign In
              </PrimaryButton>

              <SecondaryButton as={Link} to="/register" userIcon>
                Create Account
              </SecondaryButton>
            </div>

            <a className="samplelink" href="#how">
              Just exploring? See how it works ↓
            </a>
          </div>
        </div>
      </section>

      <div className="trust">
        <span>Supreme Court of India</span>
        <span>Answers with judgment citations</span>
        <span>The original source, always in view</span>
      </div>

      <section id="features" className="sec">
        <h2>Less searching in circles. More following the reasoning.</h2>

        <p className="sub">
          A focused workspace for exploring Supreme Court judgments, without
          losing the line between an AI summary and the authority itself.
        </p>

        <div className="grid4">
          {FEATURES.map(([Icon, title, text]) => (
            <article key={title} className="card feat">
              <Icon size={22} color="#ff4a17" />
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="how" className="sec dark">
        <h2>A question. A source. A clearer next step.</h2>

        <ol className="steps">
          {STEPS.map(([title, text], i) => (
            <li key={title}>
              <i>{i + 1}</i>
              <h3>{title}</h3>
              <p>{text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="sec">
        <h2>Different questions. The same care.</h2>

        <div className="grid3">
          {AUDIENCE.map(([Icon, title, text]) => (
            <article key={title} className="who">
              <Icon size={20} />
              <h3>{title}</h3>
              <p>{text}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="sec note">
        <ShieldAlert size={22} color="#ff4a17" />

        <div>
          <h2>Designed to assist. Not decide.</h2>
          <p>
            Citations make verification possible, but AI can still misread, omit
            context or make mistakes. Nyaya-Mitra is a starting point, not
            exhaustive research, and it does not replace a qualified legal
            professional or your own assessment.
          </p>
        </div>
      </section>

      <section className="sec faq">
        <h2>A few things worth knowing.</h2>

        {FAQ.map(([q, a]) => (
          <details key={q}>
            <summary>{q}</summary>
            <p>{a}</p>
          </details>
        ))}
      </section>

      <section className="final">
        <h2>Your next line of inquiry starts here.</h2>
        <p>Make space for clear thinking. Create your Nyaya-Mitra account.</p>

        <div className="row">
          <PrimaryButton as={Link} to="/login">
            Sign In
          </PrimaryButton>

          <SecondaryButton as={Link} to="/register" userIcon>
            Create Account
          </SecondaryButton>
        </div>
      </section>

      <footer className="foot">
        © 2026 Nyaya-Mitra · Research assistance, not legal advice. Always
        verify the original judgment.
      </footer>
    </div>
  );
}
