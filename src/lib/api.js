// The only file that talks to the backend (FastAPI). Every call is JSON
// over fetch.

const BASE = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');
const TIMEOUT_MS = 60000;
const TOKEN_KEY = 'nm_token';
const ADMIN_KEY = 'nm_admin_token';

if (!BASE && import.meta.env.DEV) {
  console.error('VITE_API_URL is not set. Copy .env.example to .env.');
}

/** User session. "Remember me" -> localStorage, otherwise sessionStorage. */
export const tokenStore = {
  get: () =>
    sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY),
  set: (token, remember = true) => {
    tokenStore.clear();
    (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, token);
  },
  clear: () => {
    localStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_KEY);
  },
};

/** Admin session: tab-scoped, gone when the tab closes. */
export const adminToken = {
  get: () => sessionStorage.getItem(ADMIN_KEY),
  set: (token) => sessionStorage.setItem(ADMIN_KEY, token),
  clear: () => sessionStorage.removeItem(ADMIN_KEY),
};

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
  }
  return text.trim().slice(0, 200) || GENERIC_ERROR;
}

async function request(path, { method = 'GET', body } = {}) {
  const isAdmin = path.startsWith('/admin');
  const token = isAdmin ? adminToken.get() : tokenStore.get();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token && { Authorization: `Bearer ${token}` }),
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
  // A 401 on a request that carried a token means the session ended.
  // (A wrong password also returns 401, but no token was sent then.)
  if (res.status === 401 && token && path !== '/auth/logout') {
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
  return post('/chat/query', {
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
}

export const api = {
  // Auth
  login: (email, password) => post('/auth/login', { email, password }),
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

  // Admin
  adminLogin: (username, password) =>
    post('/admin/login', { username, password }),
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
