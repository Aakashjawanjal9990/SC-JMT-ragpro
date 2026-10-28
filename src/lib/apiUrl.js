/**
 * Validate the public API base URL used by the browser.
 * The backend compatibility router is mounted under /api.
 */
export function validateProductionApiUrl(rawValue) {
  if (typeof rawValue !== 'string' || !rawValue.trim()) {
    throw new Error('VITE_API_URL must be set for a production build.');
  }

  let url;

  try {
    url = new URL(rawValue);
  } catch {
    throw new Error('VITE_API_URL must be a valid absolute URL.');
  }

  if (url.protocol !== 'https:') {
    throw new Error('VITE_API_URL must use HTTPS in production.');
  }

  if (url.username || url.password) {
    throw new Error('VITE_API_URL must not contain embedded credentials.');
  }

  if (url.search || url.hash) {
    throw new Error(
      'VITE_API_URL must not contain a query string or fragment.',
    );
  }

  const normalizedPath = url.pathname.replace(/\/+$/, '');

  if (normalizedPath !== '/api') {
    throw new Error(
      'VITE_API_URL must end with /api unless a documented same-origin proxy is configured.',
    );
  }

  return url;
}
