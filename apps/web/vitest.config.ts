import { fileURLToPath } from 'node:url';

import { configDefaults, defineConfig } from 'vitest/config';

export default defineConfig({
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  test: {
    exclude: [...configDefaults.exclude, 'e2e/**'],
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    clearMocks: true,
    restoreMocks: true,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      reporter: ['text', 'html', 'lcov'],
    },
  },
});
