import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// One self-contained file: React + the widget; drop it in any page with a <script type="module">.
export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': '"production"' },
  build: {
    lib: { entry: 'src/index.ts', formats: ['es'], fileName: () => 'narranexus-chat.js' },
    outDir: 'dist',
    rollupOptions: { output: { inlineDynamicImports: true } },
    sourcemap: true,
  },
});
