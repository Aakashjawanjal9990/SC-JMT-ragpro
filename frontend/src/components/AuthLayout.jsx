import { Link } from 'react-router-dom';
import Logo from './Logo';

const POINTS = [
  'Ground your questions in judgments',
  'Read answers with source context',
  'Build on your research, one question at a time',
];

/**
 * Shared layout for sign in, register and forgot password.
 * Beige brand panel on the left, cream form panel on the right.
 */
export default function AuthLayout({
  eyebrow,
  title,
  intro,
  topLink,
  onSubmit,
  children,
  bottom,
}) {
  return (
    <div className="auth">
      <aside className="auth-side">
        <Logo />
        <div className="side-body">
          <p className="kicker">Supreme Court judgment research</p>
          <h2>A clearer view of the law.</h2>
          <p className="side-lead">
            Ask considered questions. Follow the citations. Return to the
            judgment.
          </p>
          <ol className="points">
            {POINTS.map((point, i) => (
              <li key={point}>
                <b>{String(i + 1).padStart(2, '0')}</b>
                {point}
              </li>
            ))}
          </ol>
        </div>
        <p className="side-foot">
          Made for thoughtful research. Not a substitute for professional legal
          advice.
        </p>
      </aside>

      <main className="auth-main">
        <header className="auth-top">
          <Link to="/" className="back">
            ← Back to Nyaya-Mitra
          </Link>
          {topLink && (
            <Link to={topLink.to} className="accent">
              {topLink.label} ↗
            </Link>
          )}
        </header>

        <div className="auth-center">
          <form className="auth-form" onSubmit={onSubmit} noValidate>
            <p className="kicker">{eyebrow}</p>
            <h1>{title}</h1>
            {intro && <p className="intro">{intro}</p>}
            {children}
            {bottom && <p className="auth-bottom">{bottom}</p>}
          </form>
        </div>

        <p className="auth-foot">Nyaya-Mitra · Research with perspective.</p>
      </main>
    </div>
  );
}
