import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { validateProductionApiUrl } from './src/lib/apiUrl.js';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');

  if (mode === 'production') {
    validateProductionApiUrl(env.VITE_API_URL);
  }

  return {
    plugins: [react()],
  };
});
