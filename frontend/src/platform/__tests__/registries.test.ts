/**
 * @file_name: registries.test.ts
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: The sixteen registry names are a contract: this file is the hand-written list, and both consumers (host.ts's per-plugin facade, loader.ts's disable sweep) must cover exactly it.
 *
 * Why hand-written: `REGISTRY_NAMES` below is deliberately NOT derived from
 * `REGISTRIES` — a test that reads the same object it is checking passes for
 * any content, including a registry silently deleted along with its slot. The
 * only guard on these names was `plugins/builtin.ui/tests/test_ui_package.py`,
 * which text-parses `registries/index.ts` from Python: it catches a name that
 * drifts from the manifest, but nothing asserted that `HostAPI.registries`
 * exposes all sixteen (`host.test.tsx` only walks `commands`) or that
 * `disableBuiltinUi` sweeps all sixteen. Dropping a registry from the table AND
 * from the manifest together used to be green on both sides.
 */
import { describe, expect, it } from 'vitest';

import { REGISTRIES } from '../registries';
import { createHostApi } from '../host';
import { disableBuiltinUi } from '../loader';

/** The eighteen `ui.*` extension points, written out. Adding a nineteenth is an edit here too. */
const REGISTRY_NAMES = [
  'agentCardBadges',
  'artifactKinds',
  'channels',
  'chatHeaderActions',
  'commands',
  'composerExtensions',
  'conversationKinds',
  'messageActions',
  'messageRenderers',
  'pages',
  'panels',
  'settingsSections',
  'sidebar',
  'sidebarSections',
  'themes',
  'timelineEvents',
  'toolRenderers',
  'topBarItems',
] as const;

/** A value each registry will accept. Only `themes` validates its payload today; the rest
 *  take anything, and the point of this probe is the NAME coverage, not the shape. */
const PROBE: Partial<Record<(typeof REGISTRY_NAMES)[number], unknown>> = {
  themes: { tokens: {} },
  // artifactKinds validates its descriptor; the smallest valid one.
  artifactKinds: { renderer: () => null, editSurface: 'none', saveMode: null, selectionToAI: false, preview: 'none' },
};
const probeFor = (name: (typeof REGISTRY_NAMES)[number]) => PROBE[name] ?? {};

describe('the eighteen registry names', () => {
  it('REGISTRIES holds exactly them', () => {
    expect(Object.keys(REGISTRIES).sort()).toEqual([...REGISTRY_NAMES]);
  });

  it('host.ts exposes every one to a plugin', () => {
    const host = createHostApi('acme.probe', '1.0.0');
    expect(Object.keys(host.registries).sort()).toEqual([...REGISTRY_NAMES]);
    // Every handle is a real one, not a placeholder: registering through it and
    // disposing must round-trip.
    for (const name of REGISTRY_NAMES) {
      const handle = host.registries[name] as { register: (id: string, value: unknown) => { dispose: () => void }; list: () => unknown[] };
      const d = handle.register('probe_row', probeFor(name) as never);
      expect(handle.list().length).toBeGreaterThan(0);
      d.dispose();
    }
  });

  it('loader.ts sweeps every one when a builtin UI is disabled', () => {
    const host = createHostApi('acme.sweep', '1.0.0');
    for (const name of REGISTRY_NAMES) {
      (host.registries[name] as { register: (id: string, value: unknown) => unknown }).register('sweep_row', probeFor(name) as never);
    }
    const removed = disableBuiltinUi('acme.sweep');
    // One removal per registry: `disableBuiltinUi` returns `kind:id` strings, so
    // a registry missing from SHELL_REGISTRIES shows up as a missing entry here.
    expect(removed.map((r) => r.split(':')[1]).filter((id) => id === 'sweep_row')).toHaveLength(REGISTRY_NAMES.length);
  });
});
