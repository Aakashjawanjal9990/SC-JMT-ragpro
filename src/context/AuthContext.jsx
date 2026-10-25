import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { api } from '../lib/api';

const AuthContext = createContext(null);

export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  // Ends the session on the server (which clears the cookie), then here.
  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Nothing more to do: sign out on this page regardless.
    }
    setUser(null);
  }, []);

  // The session cookie is HttpOnly, so the page cannot tell whether it is
  // signed in. Ask the server who it is on every page load.
  useEffect(() => {
    api
      .me()
      .catch(() => null)
      .then(setUser)
      .finally(() => setLoading(false));
  }, []);

  // A 401 on any other call means the session ended on the server.
  useEffect(() => {
    const clear = () => setUser(null);
    window.addEventListener('nm:unauthorized', clear);
    return () => window.removeEventListener('nm:unauthorized', clear);
  }, []);

  // By now the backend has set the session cookie. Use the user in the reply
  // or, when there is none, ask /auth/me so Home can render.
  const accept = useCallback(async (res) => {
    setUser(res?.user || (await api.me()));
  }, []);

  const login = useCallback(
    async (email, password, remember) =>
      accept(await api.login(email, password, remember)),
    [accept],
  );
  // The backend either signs the person in at once or asks for email
  // verification first ({verificationRequired: true}).
  const register = useCallback(
    async (payload) => {
      const res = await api.register(payload);
      if (res?.verificationRequired) return { verified: false };
      await accept(res);
      return { verified: true };
    },
    [accept],
  );
  const verify = useCallback(
    async (email, code) => accept(await api.verify(email, code)),
    [accept],
  );

  const value = useMemo(
    () => ({ user, loading, login, register, verify, logout }),
    [user, loading, login, register, verify, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
