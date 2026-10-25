// The only file that talks to the backend (FastAPI). Every call is JSON
// over fetch.
//
// Sign-in is an HttpOnly session cookie set by the backend. This code never
// sees, stores or sends a token: the browser attaches the cookie itself
// because every request uses `credentials: 'include'`.

const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');
const TIMEOUT_MS = 60000;

// CSRF: state-changing requests echo the backend's CSRF token in a header.
// Names are configurable so they can match whatever the backend settles on.
const CSRF_HEADER = import.meta.env.VITE_CSRF_HEADER || 'X-CSRF-Token';
const CSRF_COOKIE = import.meta.env.VITE_CSRF_COOKIE || 'csrf_token';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

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

// The CSRF token is not a secret from scripts on this page (that is the
// point of the double-submit pattern), so keeping it in memory is fine.
let csrfFromHeader = '';

function csrfToken() {
  const hit = document.cookie
    .split('; ')
    .find((part) => part.startsWith(`${CSRF_COOKIE}=`));
  const fromCookie = hit
    ? decodeURIComponent(hit.slice(CSRF_COOKIE.length + 1))
    : '';
  return fromCookie || csrfFromHeader;
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
  const csrf = SAFE_METHODS.has(method) ? '' : csrfToken();
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
        ...(csrf && { [CSRF_HEADER]: csrf }),
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
  // Needs the backend to expose the header to the browser (CORS).
  csrfFromHeader = res.headers.get(CSRF_HEADER) || csrfFromHeader;
  const data = await res.json().catch(() => ({}));
  // A 401 anywhere else means the session cookie is missing or has expired.
  if (res.status === 401 && !PLAIN_401.has(path)) {
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
  me: () => request('/auth/me'),
  logout: () => post('/auth/logout', {}),

  // Chat
  query: (question, conversationId, think = false) =>
    queryWithInterpretation(question, conversationId, think),
  conversations: () => request('/chat/conversations'),
  conversation: (id) =>
    request(`/chat/conversations/${encodeURIComponent(id)}`),
  deleteConversation: (id) =>
    request(`/chat/conversations/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),

  // Admin
  adminLogin: (username, password) =>
    post('/admin/login', { username, password }),
  adminLogout: () => post('/admin/logout', {}),
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
