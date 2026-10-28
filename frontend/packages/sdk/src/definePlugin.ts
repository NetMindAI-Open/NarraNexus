/**
 * @file_name: definePlugin.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: `definePlugin({ activate, deactivate })` — the typed shape of a plugin bundle's default export.
 */
import type { HostAPI } from './types';

export interface PluginDefinition {
  activate(host: HostAPI): void | Promise<void>;
  deactivate?(host: HostAPI): void | Promise<void>;
}

/** Identity function that pins the type; the loader calls `plugin.activate(host)`. */
export function definePlugin(definition: PluginDefinition): PluginDefinition {
  if (typeof definition.activate !== 'function') throw new TypeError('definePlugin: activate(host) is required');
  return definition;
}
