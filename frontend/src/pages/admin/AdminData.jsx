import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';

const isoDay = (d) =>
  [d.getFullYear(), d.getMonth() + 1, d.getDate()]
    .map((n) => String(n).padStart(2, '0'))
    .join('-');

const today = () => isoDay(new Date());

const daysAgo = (days) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return isoDay(d);
};

const dateFmt = (value) =>
  value ? new Date(value).toLocaleString('en-IN') : 'Not recorded';

const label = (value) =>
  value
    ? String(value)
        .replace(/[_-]+/g, ' ')
        .replace(/^./, (c) => c.toUpperCase())
    : '-';

function Table({ headers, rows, empty }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows
          ) : (
            <tr>
              <td colSpan={headers.length} className="muted">
                {empty}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Trend({ data }) {
  if (!data?.length || data.every((item) => item.queries === 0)) {
    return (
      <p className="muted">No query events recorded for this date range.</p>
    );
  }

  const max = Math.max(...data.map((item) => item.queries), 1);

  const points = data
    .map((item, index) => {
      const x = data.length === 1 ? 300 : (index / (data.length - 1)) * 600;
      const y = 130 - (item.queries / max) * 110;
      return `${x},${y}`;
    })
    .join(' ');

  return (
    <div className="admin-trend">
      <svg viewBox="0 0 600 160" role="img" aria-label="Daily query activity">
        <polyline
          points={points}
          fill="none"
          stroke="#ff4a17"
          strokeWidth="3"
        />
      </svg>

      <div className="admin-trend-labels">
        {data.map((item) => (
          <span key={item.date}>
            {item.date.slice(5)} · {item.queries}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function AdminData() {
  const [page, setPage] = useState(1);
  const [pageSize] = useState(10);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const [start, setStart] = useState(() => daysAgo(6));
  const [end, setEnd] = useState(today);

  const [users, setUsers] = useState(null);
  const [activity, setActivity] = useState(null);
  const [sessions, setSessions] = useState(null);
  const [analytics, setAnalytics] = useState(null);

  const [loading, setLoading] = useState(true);
  const [errors, setErrors] = useState({});
  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;

    setLoading(true);
    setErrors({});

    const results = await Promise.allSettled([
      api.adminUsers({ page, pageSize, search, status }),
      api.adminAuthActivity(),
      api.adminSessions(),
      api.adminAnalytics(start, end),
    ]);

    // Do not apply results from an older filter or request.
    if (generation !== loadGeneration.current) return;

    const nextErrors = {};

    const [usersResult, activityResult, sessionsResult, analyticsResult] =
      results;

    if (usersResult.status === 'fulfilled') {
      setUsers(usersResult.value);
    } else {
      nextErrors.users =
        usersResult.reason?.message || 'Could not load user management data.';
    }

    if (activityResult.status === 'fulfilled') {
      setActivity(activityResult.value);
    } else {
      nextErrors.activity =
        activityResult.reason?.message ||
        'Could not load authentication activity.';
    }

    if (sessionsResult.status === 'fulfilled') {
      setSessions(sessionsResult.value);
    } else {
      nextErrors.sessions =
        sessionsResult.reason?.message || 'Could not load session data.';
    }

    if (analyticsResult.status === 'fulfilled') {
      setAnalytics(analyticsResult.value);
    } else {
      nextErrors.analytics =
        analyticsResult.reason?.message || 'Could not load analytics.';
    }

    setErrors(nextErrors);
    setLoading(false);
  }, [page, pageSize, search, status, start, end]);

  useEffect(() => {
    load();
  }, [load]);

  const panelError = (key) =>
    errors[key] ? (
      <div className="error" role="alert">
        <p>{errors[key]}</p>
        <button className="ghost" type="button" onClick={load}>
          Retry
        </button>
      </div>
    ) : null;

  const searchUsers = (event) => {
    event.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

  return (
    <section className="admin-data">
      {loading && (
        <p className="muted" aria-live="polite">
          Loading admin data…
        </p>
      )}

      <section className="card pad">
        <h3>User management</h3>

        {panelError('users')}

        <form className="admin-filters" onSubmit={searchUsers}>
          <input
            aria-label="Search users"
            placeholder="Search name or email"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
          />

          <select
            aria-label="Filter account status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
          >
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="pending">Pending</option>
            <option value="disabled">Disabled</option>
          </select>

          <button className="ghost" type="submit">
            Search
          </button>
        </form>

        <Table
          headers={['User ID', 'Name', 'Email', 'Registered', 'Status']}
          empty={
            errors.users
              ? 'User data could not be loaded.'
              : loading
                ? 'Loading users…'
                : 'No matching users.'
          }
          rows={(users?.items || []).map((user) => (
            <tr key={user.id}>
              <td>{user.id}</td>
              <td>{user.name}</td>
              <td>{user.email}</td>
              <td>{dateFmt(user.registered_at)}</td>
              <td>{label(user.status)}</td>
            </tr>
          ))}
        />

        <div className="admin-pagination">
          <span className="muted">{users?.total ?? 0} users</span>

          <button
            className="ghost"
            disabled={page <= 1}
            onClick={() => setPage((value) => value - 1)}
          >
            Previous
          </button>

          <span>
            Page {page} of{' '}
            {Math.max(1, Math.ceil((users?.total || 0) / pageSize))}
          </span>

          <button
            className="ghost"
            disabled={page * pageSize >= (users?.total || 0)}
            onClick={() => setPage((value) => value + 1)}
          >
            Next
          </button>
        </div>
      </section>

      <section className="card pad">
        <h3>Authentication activity</h3>

        {panelError('activity')}

        <Table
          headers={[
            'Event',
            'User ID',
            'Session ID',
            'Timestamp',
            'Logout time',
            'Status',
          ]}
          empty={
            errors.activity
              ? 'Authentication activity could not be loaded.'
              : loading
                ? 'Loading events…'
                : 'No authentication events recorded.'
          }
          rows={(activity || []).map((event) => (
            <tr key={event.id}>
              <td>{label(event.event_type)}</td>
              <td>{event.user_id}</td>
              <td>{event.session_id}</td>
              <td>{dateFmt(event.occurred_at)}</td>
              <td>{dateFmt(event.logout_at)}</td>
              <td>{label(event.event_status)}</td>
            </tr>
          ))}
        />
      </section>

      <section className="card pad">
        <h3>Sessions</h3>

        {panelError('sessions')}

        <Table
          headers={[
            'Session ID',
            'User',
            'User ID',
            'Created',
            'Last activity',
            'Expires',
            'Status',
          ]}
          empty={
            errors.sessions
              ? 'Session data could not be loaded.'
              : loading
                ? 'Loading sessions…'
                : 'No sessions recorded.'
          }
          rows={(sessions || []).map((session) => (
            <tr key={session.session_id}>
              <td>{session.session_id}</td>
              <td>
                {session.name} · {session.email}
              </td>
              <td>{session.user_id}</td>
              <td>{dateFmt(session.created_at)}</td>
              <td>{dateFmt(session.last_activity)}</td>
              <td>{dateFmt(session.expires_at)}</td>
              <td>{label(session.status)}</td>
            </tr>
          ))}
        />
      </section>

      <section className="card pad">
        <div className="admin-section-head">
          <h3>Query analytics</h3>

          <div className="admin-filters">
            <label>
              From{' '}
              <input
                type="date"
                value={start}
                max={end}
                onChange={(event) => setStart(event.target.value)}
              />
            </label>

            <label>
              To{' '}
              <input
                type="date"
                value={end}
                min={start}
                max={today()}
                onChange={(event) => setEnd(event.target.value)}
              />
            </label>
          </div>
        </div>

        {panelError('analytics')}

        <p>
          <b>{analytics?.totalQueries ?? 0}</b> queries in selected range
        </p>

        {analytics?.source && (
          <p className="muted small">Source: {analytics.source}</p>
        )}

        <Trend data={analytics?.daily || []} />
      </section>
    </section>
  );
}
