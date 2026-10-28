import { definePlugin } from '@narranexus/sdk';

export const plugin = definePlugin({
  activate(host) {
    const { react } = host.libs;
    const Panel = ({ agentId }: { agentId: string }) => react.createElement('div', { className: 'p-4' }, `__DISPLAY_NAME__ for ${agentId}`);
    host.register(host.registries.panels, '__PLUGIN_ID__.panel', { component: Panel });
  },
});
