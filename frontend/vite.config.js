import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.BACKEND_URL || 'http://127.0.0.1:8081';
  const proxy = Object.fromEntries(['/api', '/health', '/docs', '/redoc', '/openapi.json'].map(path => [path, { target, changeOrigin: true }]));
  return { plugins: [react()], server: { port: 5173, strictPort: true, proxy }, preview: { port: 4173, proxy } };
});
