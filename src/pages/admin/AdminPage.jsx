import { useEffect, useState } from 'react';
import { adminToken } from '../../lib/api';
import AdminConsole from './AdminConsole';
import AdminLogin from './AdminLogin';

/** /admin always asks for admin credentials unless this tab already has them. */
export default function AdminPage() {
  const [authed, setAuthed] = useState(() => Boolean(adminToken.get()));

  useEffect(() => {
    const lock = () => setAuthed(false);
    window.addEventListener('nm:admin-unauthorized', lock);
    return () => window.removeEventListener('nm:admin-unauthorized', lock);
  }, []);

  const signOut = () => {
    adminToken.clear();
    setAuthed(false);
  };

  return authed ? (
    <AdminConsole onSignOut={signOut} />
  ) : (
    <AdminLogin onSuccess={() => setAuthed(true)} />
  );
}
