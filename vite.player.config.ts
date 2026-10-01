// 独立展示器的 Vite 构建配置：产物使用相对路径，便于放进发布包离线运行。
import { defineConfig } from 'vite';
export default defineConfig({
  root: process.cwd(),
  base: './',
  // 发布包只包含展示器本身：不复制 public/（旧工作台的静态素材不属于播放器）。
  publicDir: false,
  build: {
    outDir: 'dist-player',
    emptyOutDir: true,
    rollupOptions: { input: { player: 'player.html' } },
  },
});
