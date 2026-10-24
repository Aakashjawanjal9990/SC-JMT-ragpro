import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // A production bundle must never fall back to localhost or plain http.
  if (mode === 'production' && !/^https:\/\/.+/.test(env.VITE_API_URL || '')) {
    throw new Error(
      'VITE_API_URL must be set to an https:// URL for a production build.',
    );
  }
  return { plugins: [react()] };
});
