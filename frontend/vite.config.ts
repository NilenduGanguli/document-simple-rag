import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  define: {
    __DEFAULT_API_KEY__: JSON.stringify(process.env.VITE_DEFAULT_API_KEY || 'dev-api-key-1'),
  },
  server: {
    port: 3001,
    proxy: {
      '/api/ingest': {
        target: 'http://localhost:18000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/ingest/, '/api/v1'),
      },
      '/api/retrieval': {
        target: 'http://localhost:18001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/retrieval/, '/api/v1'),
      },
    },
  },
});
