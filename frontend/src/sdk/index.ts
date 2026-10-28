/**
 * @file_name: index.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The host's runtime shim for `@narranexus/sdk`: the package's build-time surface plus the shared
 * components a plugin may reuse (channel config) and the theme tokens. Plugins compile against the package;
 * at runtime the loader serves this module for the same specifier.
 */
export { definePlugin, HOST_EXTERNALS, hostShimModule, vitePreset } from '@narranexus/sdk';
export type { PluginDefinition, VitePresetOptions } from '@narranexus/sdk';
export type * from '@narranexus/sdk';
export { GenericChannelConfig } from '@/components/awareness/GenericChannelConfig';
export { makeGenericChannelConfig } from '@/components/awareness/genericChannelFactory';
export { THEME_TOKENS } from '@/platform/registries/themeTokens.generated';
