/**
 * @file_name: i18n.ts
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Plugin i18n — each plugin's strings live in their own `plugin:<id>` namespace.
 *
 * The shell's strings all sit in the default `translation` namespace with
 * dotted keys; a plugin adding to that namespace could collide with or
 * override shell keys. Namespacing per plugin id makes collisions
 * impossible and lets a plugin be unloaded without leaving strings behind.
 * Lookups pass the namespace as an option (`t(key, { ns })`) rather than in
 * the key string, because the namespace itself contains i18next's `:`; the
 * key is looked up with `nsSeparator: false` so a plugin key may contain
 * `:` too. The key separator is still i18next's default `.`: `a.b` is the
 * nested path `{a: {b}}`, exactly as in the shell's own bundles.
 */
import i18n from 'i18next';

export function pluginNamespace(pluginId: string): string {
  return `plugin:${pluginId}`;
}

/** Add (deep-merged, overwriting) one language's strings for a plugin. */
export function addPluginBundle(pluginId: string, lng: string, resources: Record<string, unknown>): void {
  // The shell's i18n initialises at startup, before any plugin activates;
  // adding earlier is a programming error and fails loudly rather than
  // corrupting the store.
  if (!i18n.isInitialized) {
    throw new Error(`addPluginBundle(${pluginId}): i18n is not initialised yet`);
  }
  i18n.addResourceBundle(lng, pluginNamespace(pluginId), resources, true, true);
}

/** Remove every language bundle a plugin added. */
export function removePluginBundles(pluginId: string): void {
  const ns = pluginNamespace(pluginId);
  for (const lng of Object.keys(i18n.store?.data ?? {})) {
    if (i18n.hasResourceBundle(lng, ns)) i18n.removeResourceBundle(lng, ns);
  }
}

/** Translate a plugin's key in its namespace (falls back to the key itself). */
export function pluginT(pluginId: string, key: string, options: Record<string, unknown> = {}): string {
  return i18n.t(key, { ...options, ns: pluginNamespace(pluginId), nsSeparator: false });
}
