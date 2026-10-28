/**
 * @file_name: index.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: `@narranexus/sdk` — what a frontend plugin imports at build time: `definePlugin`, the vite preset
 * (host libraries external, one ESM file) and the HostAPI / registry types. At runtime the host resolves this
 * specifier to its own shim (`frontend/src/sdk`), which adds the shared components (GenericChannelConfig, ...).
 */
export { definePlugin } from './definePlugin';
export type { PluginDefinition } from './definePlugin';
export { HOST_EXTERNALS, hostShimModule, vitePreset } from './vitePreset';
export type { VitePresetOptions } from './vitePreset';
export type * from './types';
