import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function ProtectedRoute({ children }) {
  const { user, loading, authError, refreshSession } = useAuth();

  const location = useLocation();

  // Wait until the initial session check finishes.
  if (loading) {
    return (
      <div className="center muted" role="status">
        Checking your session...
      </div>
    );
  }

  // Handle server/network failures separately from
  // genuine unauthenticated responses.
  if (authError) {
    return (
      <section className="error center" role="alert">
        <p>{authError}</p>

        <button
          type="button"
          className="btn btn-secondary"
          onClick={refreshSession}
        >
          Retry session check
        </button>
      </section>
    );
  }

  // Redirect only when the session check confirms
  // that the user is unauthenticated.
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return children;
}
