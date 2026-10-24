import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { PrimaryButton } from '../../components/Buttons';
import Logo from '../../components/Logo';
import TextField from '../../components/TextField';
import { api, adminToken } from '../../lib/api';

/** Separate credentials from user accounts. Checked by POST /admin/login. */
export default function AdminLogin({ onSuccess }) {
  const [form, setForm] = useState({ username: '', password: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!form.username.trim() || !form.password) {
      setError('Enter the admin username and passkey.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const { token } = await api.adminLogin(
        form.username.trim(),
        form.password,
      );
      adminToken.set(token);
      onSuccess();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  return (
    <div className="admin-gate">
      <Logo />
      <form className="card gate-card" onSubmit={submit} noValidate>
        <div className="eyebrow caps">
          <ShieldCheck size={13} /> Restricted access
        </div>
        <h1>Admin console</h1>
        <p className="muted small">
          For authorised personnel only. Sign in with your admin credentials.
        </p>

        <TextField
          label="Admin username"
          autoComplete="username"
          value={form.username}
          onChange={set('username')}
        />
        <TextField
          label="Passkey"
          type="password"
          autoComplete="current-password"
          value={form.password}
          onChange={set('password')}
        />
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        <PrimaryButton type="submit" loading={busy} disabled={busy}>
          Enter console
        </PrimaryButton>
      </form>
      <Link to="/" className="link-sm">
        ← Back to Nyaya-Mitra
      </Link>
    </div>
  );
}
