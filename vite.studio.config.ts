import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

// 制作编辑器单独构建，不携带旧游戏、旧工作台与 public 示例素材。
export default defineConfig({
  plugins: [vue()],
  base: './',
  publicDir: false,
  build: {
    outDir: 'dist-editor',
    emptyOutDir: true,
    rollupOptions: { input: { 'studio-live': 'studio-live.html' } },
  },
});
