import { defineConfig } from 'vite';

// Runtime part of the SDK (definePlugin + the preset helpers); types come from tsconfig.build.json.
export default defineConfig({
  build: {
    lib: { entry: 'src/index.ts', formats: ['es'], fileName: () => 'index.js' },
    outDir: 'dist',
    emptyOutDir: false,
    sourcemap: true,
    minify: false,
  },
});
