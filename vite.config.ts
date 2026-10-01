import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig({
  plugins: [vue()],
  server: { port: 5173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    rollupOptions: {
      input: { game: 'index.html', studio: 'studio.html', 'studio-live': 'studio-live.html' },
      output: { manualChunks: { ink: ['inkjs'] } },
    },
  },
});
