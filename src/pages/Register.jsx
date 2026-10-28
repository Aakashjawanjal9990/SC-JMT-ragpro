import { useState } from 'react';
import { Link, Navigate, useNavigate } from 'react-router-dom';
import AuthLayout from '../components/AuthLayout';
import { PrimaryButton } from '../components/Buttons';
import OtpField from '../components/OtpField';
import TextField from '../components/TextField';
import { useAuth } from '../context/AuthContext';
import useCountdown from '../hooks/useCountdown';
import { api } from '../lib/api';
import {
  maskEmail,
  validateCode,
  validateEmail,
  validateName,
  validatePassword,
} from '../lib/validators';

/** Shown only when the backend asks for email verification. */
function VerifyStep({ email, onBack }) {
  const { verify } = useAuth();
  const navigate = useNavigate();
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resendBusy, setResendBusy] = useState(false);
  const [left, restart] = useCountdown(30);

  const submit = async (e) => {
    e.preventDefault();

    const invalid = validateCode(code);
    setError(invalid);

    if (invalid || busy) return;

    setBusy(true);

    try {
      await verify(email, code);
      navigate('/home', { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    if (resendBusy || left > 0) return;

    setResendBusy(true);
    setError('');

    try {
      await api.resend(email);
      restart();
    } catch (err) {
      setError(err.message);
    } finally {
      setResendBusy(false);
    }
  };

  return (
    <AuthLayout
      eyebrow="One last step"
      title="Check your email."
      intro={`We sent a 6-digit code to ${maskEmail(email)}.`}
      topLink={{ label: 'Already registered? Sign In', to: '/login' }}
      onSubmit={submit}
      bottom={
        <>
          {left > 0 ? (
            `Resend code in ${left}s`
          ) : (
            <button
              type="button"
              className="linkbtn"
              onClick={resend}
              disabled={resendBusy}
            >
              {resendBusy ? 'Sending…' : 'Resend code'}
            </button>
          )}
          <span className="fineprint">
            Wrong email?{' '}
            <button type="button" className="linkbtn" onClick={onBack}>
              Go back
            </button>
          </span>
        </>
      }
    >
      <OtpField value={code} onChange={setCode} error={error} />
      <PrimaryButton type="submit" loading={busy} disabled={busy}>
        Verify and continue
      </PrimaryButton>
    </AuthLayout>
  );
}

export default function Register() {
  const { user, register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    name: '',
    email: '',
    password: '',
    confirm: '',
  });
  const [agree, setAgree] = useState(false);
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingEmail, setPendingEmail] = useState(null);

  if (user) return <Navigate to="/home" replace />;

  const rules = {
    name: validateName,
    email: validateEmail,
    password: validatePassword,
    confirm: (value) =>
      value === form.password ? '' : 'Passwords do not match.',
  };

  const set = (key) => (e) => {
    setForm({ ...form, [key]: e.target.value });

    if (errors[key]) {
      setErrors({ ...errors, [key]: '' });
    }
  };

  const validateOne = (key) => () =>
    setErrors((prev) => ({
      ...prev,
      [key]: form[key] ? rules[key](form[key]) : '',
    }));

  const submit = async (e) => {
    e.preventDefault();
    setFormError('');

    if (busy) return;

    const next = {};

    Object.keys(rules).forEach((key) => {
      const message = rules[key](form[key]);
      if (message) next[key] = message;
    });

    if (!agree) next.agree = 'Please confirm to continue.';

    setErrors(next);

    if (Object.keys(next).length) return;

    const email = form.email.trim().toLowerCase();

    setBusy(true);

    try {
      const result = await register({
        name: form.name.trim(),
        email,
        password: form.password,
      });

      if (result.verified) {
        navigate('/home', { replace: true });
      } else {
        setPendingEmail(email);
      }
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (pendingEmail) {
    return (
      <VerifyStep email={pendingEmail} onBack={() => setPendingEmail(null)} />
    );
  }

  return (
    <AuthLayout
      eyebrow="A new line of inquiry"
      title="Create your account."
      intro="Your own space for thoughtful legal research."
      topLink={{ label: 'Already registered? Sign In', to: '/login' }}
      onSubmit={submit}
      bottom={
        <>
          Already have an account? <Link to="/login">Sign in ↗</Link>
        </>
      }
    >
      <TextField
        label="Full name"
        autoComplete="name"
        value={form.name}
        onChange={set('name')}
        onBlur={validateOne('name')}
        error={errors.name}
      />

      <TextField
        label="Email address"
        type="email"
        autoComplete="email"
        value={form.email}
        onChange={set('email')}
        onBlur={validateOne('email')}
        error={errors.email}
      />

      <TextField
        label="Password"
        type="password"
        autoComplete="new-password"
        value={form.password}
        onChange={set('password')}
        onBlur={validateOne('password')}
        error={errors.password}
        hint="Use at least 8 characters with a letter and a number."
      />

      <TextField
        label="Confirm password"
        type="password"
        autoComplete="new-password"
        value={form.confirm}
        onChange={set('confirm')}
        onBlur={validateOne('confirm')}
        error={errors.confirm}
      />

      <label className="check top">
        <input
          type="checkbox"
          checked={agree}
          onChange={(e) => {
            setAgree(e.target.checked);
            setErrors({ ...errors, agree: '' });
          }}
        />
        I understand that Nyaya-Mitra provides research assistance, not legal
        advice.
      </label>

      {errors.agree && <span className="field-error">{errors.agree}</span>}

      {formError && (
        <div className="error" role="alert">
          {formError}
        </div>
      )}

      <PrimaryButton type="submit" loading={busy} disabled={busy}>
        Create Account
      </PrimaryButton>
    </AuthLayout>
  );
}
