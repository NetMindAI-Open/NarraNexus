/**
 * @file_name: actionGate.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The gate entry for a manifest-declared action slot (chat header / message actions): the declared label activates the plugin, then runs the real action registered under the same id.
 *
 * Lives apart from gates.tsx because it produces a plain object, not a
 * component (react-refresh wants component-only modules there).
 */
import { fireActivation } from './activation';
import type { Registry, SlotActionDef } from './registries';

export function makeActionGate(pluginId: string, slotId: string, registry: Registry<SlotActionDef>, label: string, when?: string[], order?: number): SlotActionDef {
  const gate: SlotActionDef = {
    label,
    when,
    order,
    run: async (ctx) => {
      await fireActivation(`onSlot:${slotId}`);
      const real = registry.list().find((e) => e.id === slotId && e.owner === pluginId && e.value !== gate);
      if (real) await real.value.run(ctx);
    },
  };
  return gate;
}

