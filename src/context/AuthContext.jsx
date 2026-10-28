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
  const [authError, setAuthError] = useState('');

  const refreshSession = useCallback(async () => {
    setLoading(true);
    setAuthError('');

    try {
      const currentUser = await api.me();
      setUser(currentUser);
    } catch (error) {
      if (error?.status === 401) {
        setUser(null);
      } else {
        setAuthError(
          error?.message || 'Unable to check your session. Please try again.',
        );
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Clear local UI state even if the server is unreachable.
    }

    setUser(null);
    setAuthError('');
  }, []);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      setLoading(true);
      setAuthError('');

      try {
        const currentUser = await api.me();

        if (active) {
          setUser(currentUser);
        }
      } catch (error) {
        if (!active) return;

        if (error?.status === 401) {
          setUser(null);
        } else {
          setAuthError(
            error?.message || 'Unable to check your session. Please try again.',
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    bootstrap();

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null);
      setAuthError('');
    };

    window.addEventListener('nm:unauthorized', handleUnauthorized);

    return () => {
      window.removeEventListener('nm:unauthorized', handleUnauthorized);
    };
  }, []);

  const accept = useCallback(async (response) => {
    const nextUser = response?.user || (await api.me());

    setUser(nextUser);
    setAuthError('');
  }, []);

  const login = useCallback(
    async (email, password, remember) => {
      await accept(await api.login(email, password, remember));
    },
    [accept],
  );

  const register = useCallback(
    async (payload) => {
      const response = await api.register(payload);

      if (response?.verificationRequired) {
        return { verified: false };
      }

      await accept(response);
      return { verified: true };
    },
    [accept],
  );

  const verify = useCallback(
    async (email, code) => {
      await accept(await api.verify(email, code));
    },
    [accept],
  );

  const value = useMemo(
    () => ({
      user,
      loading,
      authError,
      refreshSession,
      login,
      register,
      verify,
      logout,
    }),
    [user, loading, authError, refreshSession, login, register, verify, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
