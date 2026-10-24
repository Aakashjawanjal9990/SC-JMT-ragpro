import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import AuthLayout from '../components/AuthLayout';
import { PrimaryButton } from '../components/Buttons';
import TextField from '../components/TextField';
import { useAuth } from '../context/AuthContext';
import { validateEmail } from '../lib/validators';

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ email: '', password: '' });
  const [remember, setRemember] = useState(true);
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);

  const prev = location.state?.from;
  const from = prev ? `${prev.pathname}${prev.search || ''}` : '/home';
  if (user) return <Navigate to={from} replace />;

  const set = (key) => (e) => {
    setForm({ ...form, [key]: e.target.value });
    if (errors[key]) setErrors({ ...errors, [key]: '' });
  };

  const submit = async (e) => {
    e.preventDefault();
    setFormError('');
    const next = {};
    const emailError = validateEmail(form.email);
    if (emailError) next.email = emailError;
    if (!form.password) next.password = 'Enter your password.';
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    try {
      await login(form.email.trim().toLowerCase(), form.password, remember);
      navigate(from, { replace: true });
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="Welcome back"
      title="Continue your research."
      intro="Sign in to your Nyaya-Mitra workspace."
      topLink={{ label: 'New here? Create Account', to: '/register' }}
      onSubmit={submit}
      bottom={
        <>
          New to Nyaya-Mitra? <Link to="/register">Create Account ↗</Link>
          <span className="fineprint">
            An account is required to submit research questions.
          </span>
        </>
      }
    >
      <TextField
        label="Email address"
        type="email"
        autoComplete="email"
        placeholder="you@example.com"
        value={form.email}
        onChange={set('email')}
        error={errors.email}
      />
      <TextField
        label="Password"
        type="password"
        autoComplete="current-password"
        value={form.password}
        onChange={set('password')}
        error={errors.password}
      />
      <div className="between">
        <label className="check">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />
          Remember me
        </label>
        <Link to="/forgot-password" className="link-sm">
          Forgot password?
        </Link>
      </div>
      {formError && (
        <div className="error" role="alert">
          {formError}
        </div>
      )}
      <PrimaryButton type="submit" loading={busy} disabled={busy}>
        Sign In
      </PrimaryButton>
    </AuthLayout>
  );
}
