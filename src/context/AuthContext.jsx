import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { api, tokenStore } from '../lib/api';

const AuthContext = createContext(null);

export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    api.logout().catch(() => {
      // The server expires this session even if the browser is offline.
    });
    tokenStore.clear();
    setUser(null);
  }, []);

  useEffect(() => {
    const restore = tokenStore.get()
      ? api.me().catch(() => null)
      : Promise.resolve(null);
    restore.then(setUser).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    window.addEventListener('nm:unauthorized', logout);
    return () => window.removeEventListener('nm:unauthorized', logout);
  }, [logout]);

  // Backends differ: some reply {token, user}, some only {access_token}.
  // When no user comes back, fetch it from /auth/me so Home can render.
  const accept = useCallback(async (res, remember) => {
    const token = res.token || res.access_token;
    if (!token) throw new Error('Sign-in failed. Please try again.');
    tokenStore.set(token, remember);
    try {
      setUser(res.user || (await api.me()));
    } catch (err) {
      tokenStore.clear();
      throw err;
    }
  }, []);

  const login = useCallback(
    async (email, password, remember) =>
      accept(await api.login(email, password), remember),
    [accept],
  );
  // The backend either signs the person in at once ({token, user}) or asks
  // for email verification first ({verificationRequired: true}).
  const register = useCallback(
    async (payload) => {
      const res = await api.register(payload);
      if (!res.token && !res.access_token) return { verified: false };
      await accept(res, true);
      return { verified: true };
    },
    [accept],
  );
  const verify = useCallback(
    async (email, code) => accept(await api.verify(email, code), true),
    [accept],
  );

  const value = useMemo(
    () => ({ user, loading, login, register, verify, logout }),
    [user, loading, login, register, verify, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
