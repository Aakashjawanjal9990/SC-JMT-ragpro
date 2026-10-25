// The only file that talks to the backend (FastAPI). Every call is JSON
// over fetch.
//
// Sign-in is an HttpOnly session cookie set by the backend. This code never
// sees or stores the session: the browser attaches the cookie itself because
// every request uses `credentials: 'include'`.
//
// CSRF: login and /auth/me return a `csrfToken` in the JSON body. It is kept
// in memory only (never localStorage, sessionStorage or a cookie) and sent as
// `X-CSRF-Token` on every state-changing request.

const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');
const TIMEOUT_MS = 60000;

const CSRF_HEADER = 'X-CSRF-Token';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

// Agreed backend endpoints for archiving. Change them here if the backend
// contract changes; nothing else in the app builds these URLs.
const ARCHIVE_PATH = (id) =>
  `/chat/conversations/${encodeURIComponent(id)}/archive`; // POST
const RESTORE_PATH = (id) =>
  `/chat/conversations/${encodeURIComponent(id)}/restore`; // POST
const ARCHIVED_LIST_PATH = '/chat/conversations?archived=true'; // GET

// A 401 from these paths is an ordinary answer (wrong password, nobody signed
// in yet), not "your session ended", so it must not trigger a sign-out.
const PLAIN_401 = new Set([
  '/auth/login',
  '/auth/register',
  '/auth/verify',
  '/auth/resend',
  '/auth/forgot',
  '/auth/reset',
  '/auth/me',
  '/auth/logout',
  '/admin/login',
  '/admin/logout',
]);

if (!BASE && import.meta.env.DEV) {
  console.error('VITE_API_URL is not set. Copy .env.example to .env.');
}

// In-memory CSRF tokens. A page reload clears them; AuthProvider calls
// /auth/me on every load, which brings the token back. Users and admins have
// separate sessions, so each keeps its own token.
const csrf = { user: '', admin: '' };

function rememberCsrf(scope, data) {
  if (typeof data?.csrfToken === 'string' && data.csrfToken) {
    csrf[scope] = data.csrfToken;
  }
}

/**
 * Turn a failed reply into one safe sentence for the user.
 * - 5xx: never show backend text (it may contain internal diagnostics).
 * - 4xx: show the backend's `detail`, which the API contract says is
 *   written for end users. FastAPI sends {"detail": "text"} or, for 422,
 *   {"detail": [{"msg": "..."}]}. Capped at 200 characters.
 */
const GENERIC_ERROR = 'Something went wrong. Please try again.';

function errorMessage(data, status) {
  if (status >= 500) return GENERIC_ERROR;
  const detail = data?.detail;
  let text = '';
  if (typeof detail === 'string') {
    text = detail;
  } else if (Array.isArray(detail)) {
    text = detail
      .map((item) => item?.msg)
      .filter(Boolean)
      .join(' ');
  } else if (typeof data?.message === 'string') {
    text = data.message;
  } else if (data?.error) {
    // Backend envelope: {request_id, error: {code, message, details: [{message}]}}
    const fieldMessages = Array.isArray(data.error.details)
      ? data.error.details.map((d) => d?.message).filter(Boolean)
      : [];
    text = fieldMessages.length
      ? fieldMessages.join(' ')
      : data.error.message || '';
  }
  return text.trim().slice(0, 200) || GENERIC_ERROR;
}

async function request(path, { method = 'GET', body } = {}) {
  const isAdmin = path.startsWith('/admin');
  const scope = isAdmin ? 'admin' : 'user';
  const token = SAFE_METHODS.has(method) ? '' : csrf[scope];
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      credentials: 'include',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token && { [CSRF_HEADER]: token }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new Error(
      err.name === 'AbortError'
        ? 'The server took too long to respond. Please try again.'
        : 'Cannot reach the server. Please try again shortly.',
    );
  } finally {
    clearTimeout(timer);
  }
  const data = await res.json().catch(() => ({}));
  if (res.ok) rememberCsrf(scope, data);
  // A 401 anywhere else means the session cookie is missing or has expired.
  if (res.status === 401 && !PLAIN_401.has(path)) {
    csrf[scope] = '';
    window.dispatchEvent(
      new Event(isAdmin ? 'nm:admin-unauthorized' : 'nm:unauthorized'),
    );
  }
  if (!res.ok) {
    const error = new Error(errorMessage(data, res.status));
    error.status = res.status;
    throw error;
  }
  return data;
}

const post = (path, body) => request(path, { method: 'POST', body });

const NO_ANSWER =
  'I could not find enough support in the judgments to answer this. Try rephrasing or adding detail.';

const pageLabel = (pages) =>
  pages.length === 1
    ? String(pages[0])
    : `${pages[0]}-${pages[pages.length - 1]}`;

/** Accepts the frontend shape or the backend's citations shape. */
function normalizeQueryResponse(res) {
  let sources = res.sources;
  if (!sources && Array.isArray(res.citations)) {
    const byCase = new Map(); // one chip per judgment, pages merged
    for (const c of res.citations) {
      const name = c.case_name || c.judgment_title || c.citation || 'Judgment';
      const entry = byCase.get(name) || {
        case: name,
        year: c.decision_date ? String(c.decision_date).slice(0, 4) : '',
        pages: new Set(),
      };
      (c.original_page_numbers || []).forEach((p) => entry.pages.add(p));
      byCase.set(name, entry);
    }
    sources = [...byCase.values()].map(({ pages, ...rest }) => ({
      ...rest,
      page: pages.size ? pageLabel([...pages].sort((a, b) => a - b)) : '-',
    }));
  }
  return {
    conversationId: res.conversationId,
    answer: res.answer?.trim() || NO_ANSWER,
    sources: sources || [],
  };
}

async function queryWithInterpretation(question, conversationId, think) {
  let interpretation;
  try {
    interpretation = await post('/chat/interpret', { question });
  } catch (error) {
    if (error.status === 422) throw error;
    // /chat/interpret is optional: any other failure falls back to a
    // plain /chat/query.
  }
  if (interpretation?.clarificationNeeded) {
    return {
      conversationId,
      answer:
        interpretation.clarificationQuestion ||
        'Could you clarify the legal issue or jurisdiction?',
      sources: [],
    };
  }
  const res = await post('/chat/query', {
    question,
    conversationId,
    think,
    normalizedQuery: interpretation?.normalizedQuery || question,
    searchTerms: [
      ...(interpretation?.keywords || []),
      ...(interpretation?.alternatives || []),
    ],
    clarificationNeeded: false,
    clarificationQuestion: '',
  });
  return normalizeQueryResponse(res);
}

export const api = {
  // Auth
  login: (email, password, remember = true) =>
    post('/auth/login', { email, password, remember }),
  register: (payload) => post('/auth/register', payload),
  verify: (email, code) => post('/auth/verify', { email, code }),
  resend: (email) => post('/auth/resend', { email }),
  forgot: (email) => post('/auth/forgot', { email }),
  reset: (payload) => post('/auth/reset', payload),
  // /auth/me returns the user (optionally with `csrfToken`, and optionally
  // wrapped as {user, csrfToken}). The token is stripped so it never ends
  // up in React state.
  me: async () => {
    const { csrfToken: _token, user, ...rest } = await request('/auth/me');
    return user || rest;
  },
  hasCsrfToken: () => Boolean(csrf.user),
  logout: async () => {
    try {
      return await post('/auth/logout', {});
    } finally {
      csrf.user = '';
    }
  },

  // Chat
  query: (question, conversationId, think = false) =>
    queryWithInterpretation(question, conversationId, think),
  conversations: () => request('/chat/conversations'),
  conversation: (id) =>
    request(`/chat/conversations/${encodeURIComponent(id)}`),
  archivedConversations: () => request(ARCHIVED_LIST_PATH),
  archiveConversation: (id) => post(ARCHIVE_PATH(id), {}),
  restoreConversation: (id) => post(RESTORE_PATH(id), {}),

  // Admin
  adminLogin: (username, password) =>
    post('/admin/login', { username, password }),
  adminLogout: async () => {
    try {
      return await post('/admin/logout', {});
    } finally {
      csrf.admin = '';
    }
  },
  adminUsers: ({ page, pageSize, status, search }) => {
    const qs = new URLSearchParams({ page, pageSize, status, search });
    return request(`/admin/users?${qs}`);
  },
  adminAuthActivity: () => request('/admin/auth-activity'),
  adminSessions: () => request('/admin/sessions'),
  adminAnalytics: (start, end) => {
    const qs = new URLSearchParams({ start, end });
    return request(`/admin/analytics?${qs}`);
  },
};
