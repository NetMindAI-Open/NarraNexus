// Built with the @narranexus/sdk vite preset into frontend/dist/plugin.js (commit the build, D12).
import { definePlugin } from '@narranexus/sdk';

export const plugin = definePlugin({
  activate(host) {
    const { react } = host.libs;
    const Page = () => react.createElement('div', { className: 'p-4' }, host.i18n.t('title'));
    host.i18n.addResourceBundle('en', { title: '__DISPLAY_NAME__' });
    host.register(host.registries.pages, '__PLUGIN_ID__.home', { path: 'x/__PLUGIN_PKG__', element: Page, guard: 'protected', layout: 'app' });
    host.register(host.registries.commands, '__PLUGIN_ID__.open', { label: 'Open __DISPLAY_NAME__', run: () => history.pushState(null, '', '/app/x/__PLUGIN_PKG__') });
  },
});
