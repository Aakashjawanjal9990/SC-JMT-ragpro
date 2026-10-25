import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import AdminConsole from './AdminConsole';
import AdminLogin from './AdminLogin';

/**
 * /admin always asks for admin credentials after a page load. The admin
 * session is an HttpOnly cookie, which the page cannot inspect, so "signed
 * in" only lives in this component's memory.
 */
export default function AdminPage() {
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    const lock = () => setAuthed(false);
    window.addEventListener('nm:admin-unauthorized', lock);
    return () => window.removeEventListener('nm:admin-unauthorized', lock);
  }, []);

  const signOut = async () => {
    try {
      await api.adminLogout();
    } catch {
      // The cookie expires on its own; lock the console regardless.
    }
    setAuthed(false);
  };

  return authed ? (
    <AdminConsole onSignOut={signOut} />
  ) : (
    <AdminLogin onSuccess={() => setAuthed(true)} />
  );
}
