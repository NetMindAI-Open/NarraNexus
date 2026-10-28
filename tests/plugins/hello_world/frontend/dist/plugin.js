export const plugin = {
  activate(host) {
    const { react } = host.libs;
    host.i18n.addResourceBundle('en', { title: 'Hello World' });
    host.register(host.registries.pages, 'acme.hello_world.home', { path: 'x/hello', element: () => react.createElement('p', null, host.i18n.t('title')), guard: 'protected', layout: 'app' });
    host.register(host.registries.panels, 'acme.hello_world.panel', { component: ({ agentId }) => react.createElement('p', null, 'panel for ' + agentId) });
    host.register(host.registries.commands, 'acme.hello_world.wave', { label: 'Wave', run: () => { globalThis.__hello_waved = true; } });
    host.register(host.registries.themes, 'acme.hello_world.theme', { displayName: 'Hello dark', tokens: { '--nm-ink': '#eee' }, dark: true });
    // Slot points (batch 3d): the ids match the manifest's declared gates.
    host.register(host.registries.messageRenderers, 'acme.hello_world.greeting', { match: (m) => typeof m.content === 'string' && m.content.startsWith('hello:'), component: ({ message }) => react.createElement('div', { 'data-testid': 'hello-greeting' }, 'GREETING ' + message.content.slice(6)) });
    host.register(host.registries.timelineEvents, 'hello_wave', { component: ({ event }) => react.createElement('div', { 'data-testid': 'hello-wave' }, 'wave ' + event.id) });
    host.register(host.registries.composerExtensions, 'acme.hello_world.strip', { component: ({ agentId }) => react.createElement('button', { type: 'button' }, 'hello strip ' + agentId), when: ['conversationKind:chat'], order: 50 });
    host.register(host.registries.chatHeaderActions, 'acme.hello_world.header_wave', { label: 'Wave from header', run: () => { globalThis.__hello_header_waved = true; } });
  },
};
