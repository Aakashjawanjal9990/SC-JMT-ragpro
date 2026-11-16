import { useState } from 'react';
import { Link } from 'react-router-dom';
import AuthLayout from '../components/AuthLayout';
import { PrimaryButton } from '../components/Buttons';
import OtpField from '../components/OtpField';
import TextField from '../components/TextField';
import useCountdown from '../hooks/useCountdown';
import { api } from '../lib/api';
import {
  maskEmail,
  validateCode,
  validateEmail,
  validatePassword,
} from '../lib/validators';

export default function ForgotPassword() {
  const [step, setStep] = useState('request');
  const [email, setEmail] = useState('');
  const [target, setTarget] = useState('');
  const [form, setForm] = useState({
    code: '',
    password: '',
    confirm: '',
  });
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resendBusy, setResendBusy] = useState(false);
  const [resendNotice, setResendNotice] = useState('');
  const [left, restart] = useCountdown(30);

  const requestCode = async (e) => {
    e.preventDefault();
    setFormError('');

    const emailError = validateEmail(email);
    setErrors({ email: emailError });

    if (emailError || busy) return;

    setBusy(true);

    try {
      const normalised = email.trim().toLowerCase();

      await api.forgot(normalised);

      setTarget(normalised);
      restart();
      setStep('reset');
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const resetPassword = async (e) => {
    e.preventDefault();
    setFormError('');

    if (busy) return;

    const next = {};
    const codeError = validateCode(form.code);
    const passError = validatePassword(form.password);

    if (codeError) next.code = codeError;
    if (passError) next.password = passError;

    if (form.confirm !== form.password) {
      next.confirm = 'Passwords do not match.';
    }

    setErrors(next);

    if (Object.keys(next).length) return;

    setBusy(true);

    try {
      await api.reset({
        email: target,
        code: form.code,
        password: form.password,
      });

      setStep('done');
    } catch (err) {
      setFormError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const set = (key) => (e) => {
    const value = e.target.value;
    setForm((prev) => ({ ...prev, [key]: value }));
    setFormError('');
    setResendNotice('');

    setErrors((prev) => {
      const next = { ...prev };
      if (key === 'confirm' && (next.confirm || value)) {
        next.confirm = value === form.password ? '' : 'Passwords do not match.';
      } else if (next[key]) {
        next[key] = '';
      }
      if (key === 'password' && form.confirm) {
        next.confirm = form.confirm === value ? '' : 'Passwords do not match.';
      }
      return next;
    });
  };

  const validateResetField = (key) => () => {
    const message =
      key === 'code'
        ? validateCode(form.code)
        : key === 'password'
          ? validatePassword(form.password)
          : form.confirm === form.password
            ? ''
            : 'Passwords do not match.';
    setErrors((prev) => ({ ...prev, [key]: message }));
  };

  const resendCode = async () => {
    if (resendBusy || busy || left > 0) return;

    setResendBusy(true);
    setFormError('');
    setResendNotice('');

    try {
      await api.forgot(target);

      restart();
      setResendNotice(
        'If the address is eligible, a reset code has been sent.',
      );
    } catch (err) {
      setFormError(err.message);
    } finally {
      setResendBusy(false);
    }
  };

  if (step === 'done') {
    return (
      <AuthLayout
        eyebrow="All set"
        title="Password updated."
        intro="You can now sign in with your new password."
        topLink={{ label: 'New here? Create Account', to: '/register' }}
        onSubmit={(e) => e.preventDefault()}
      >
        <PrimaryButton as={Link} to="/login">
          Go to Sign In
        </PrimaryButton>
      </AuthLayout>
    );
  }

  if (step === 'reset') {
    return (
      <AuthLayout
        eyebrow="Reset password"
        title="Choose a new password."
        intro={`Enter the 6-digit code sent to ${maskEmail(target)}.`}
        topLink={{ label: 'Back to Sign In', to: '/login' }}
        onSubmit={resetPassword}
        bottom={
          left > 0 ? (
            `Resend code in ${left}s`
          ) : (
            <button
              type="button"
              className="linkbtn"
              disabled={resendBusy || busy}
              onClick={resendCode}
            >
              {resendBusy ? 'Sending…' : 'Resend code'}
            </button>
          )
        }
      >
        <OtpField
          value={form.code}
          disabled={busy}
          onChange={(code) => {
            setForm((prev) => ({ ...prev, code }));
            setErrors((prev) => ({ ...prev, code: '' }));
            setFormError('');
            setResendNotice('');
          }}
          onBlur={validateResetField('code')}
          error={errors.code}
        />

        <TextField
          label="New password"
          type="password"
          autoComplete="new-password"
          value={form.password}
          disabled={busy}
          onChange={set('password')}
          onBlur={validateResetField('password')}
          error={errors.password}
          hint="Use 8–128 characters with at least one letter and one number."
        />

        <TextField
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          value={form.confirm}
          disabled={busy}
          onChange={set('confirm')}
          onBlur={validateResetField('confirm')}
          error={errors.confirm}
        />

        {formError && (
          <div className="error" role="alert">
            {formError}
          </div>
        )}

        {resendNotice && (
          <p className="muted" role="status">
            {resendNotice}
          </p>
        )}

        <PrimaryButton type="submit" loading={busy} disabled={busy}>
          Update password
        </PrimaryButton>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      eyebrow="Forgot password"
      title="Let’s get you back in."
      intro="Enter the email you registered with and we will send a reset code."
      topLink={{ label: 'Back to Sign In', to: '/login' }}
      onSubmit={requestCode}
    >
      <TextField
        label="Email address"
        type="email"
        autoComplete="email"
        value={email}
        disabled={busy}
        onChange={(e) => {
          setEmail(e.target.value);
          setErrors((prev) => ({ ...prev, email: '' }));
          setFormError('');
        }}
        onBlur={() =>
          setErrors((prev) => ({ ...prev, email: validateEmail(email) }))
        }
        error={errors.email}
      />

      {formError && (
        <div className="error" role="alert">
          {formError}
        </div>
      )}

      <PrimaryButton type="submit" loading={busy} disabled={busy}>
        Send reset code
      </PrimaryButton>
    </AuthLayout>
  );
}
