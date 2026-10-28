// Prebuilt placeholder — replace with the vite preset build output.
export const plugin = { activate(host) { host.log.error(new Error('__PLUGIN_ID__: build frontend/dist/plugin.js from frontend/src/index.ts')); } };
