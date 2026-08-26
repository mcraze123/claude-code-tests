import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In dev the API runs as its own process; in production the server serves
    // this build from the same origin, so the /api paths stay identical.
    proxy: { '/api': 'http://localhost:8080' },
  },
  build: { outDir: 'dist', sourcemap: true },
});
