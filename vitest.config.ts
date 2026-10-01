import { defineConfig } from 'vitest/config';
export default defineConfig({
  test: {
    pool: 'threads',
    include: ['tests/*.test.ts', 'tests/studio/**/*.test.ts'],
    // 集成测试需要真实后台进程与 MCP 进程的启动/重启时间
    testTimeout: 60000,
    hookTimeout: 60000,
    fileParallelism: false,
  },
});
