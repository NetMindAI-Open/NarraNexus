import { definePlugin } from '@narranexus/sdk';

// Only tokens the shell declares in @theme are accepted (see the generated token list in the SDK types).
export const plugin = definePlugin({
  activate(host) {
    host.register(host.registries.themes, '__PLUGIN_ID__.theme', {
      displayName: '__DISPLAY_NAME__',
      tokens: { '--nm-paper': '#0f1115', '--nm-ink': '#e8e8e8' },
      dark: true,
    });
  },
});
