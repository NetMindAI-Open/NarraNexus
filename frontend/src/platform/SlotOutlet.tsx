/**
 * @file_name: SlotOutlet.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: Mounts a component slot point: every visible entry, in order, each isolated so one plugin's render error never takes the surface down.
 *
 * The isolation itself lives in `PluginBoundary.tsx` (extracted 2026-09-07
 * so `MessageBubble` / `TurnTimeline`'s content registries can share it —
 * see that file for why it exists as its own module).
 */
import { PluginBoundary } from './PluginBoundary';
import { useRegistryEntries, visibleSlotEntries, type Registry, type SlotComponentDef, type SlotComponentProps, type WhenContext } from './registries';

export interface SlotOutletProps extends SlotComponentProps {
  registry: Registry<SlotComponentDef>;
  ctx: WhenContext;
  className?: string;
  /** Wrapper element; omitted (default) renders the entries bare. */
  as?: 'div' | 'span';
}

export function SlotOutlet({ registry, ctx, className, as, ...props }: SlotOutletProps) {
  const entries = visibleSlotEntries(useRegistryEntries(registry), ctx);
  if (entries.length === 0) return null;
  const children = entries.map((e) => {
    const C = e.value.component;
    return (
      <PluginBoundary key={e.id} owner={e.owner}>
        <C {...props} />
      </PluginBoundary>
    );
  });
  if (!as) return <>{children}</>;
  const Wrapper = as;
  return (
    <Wrapper className={className} data-slot={registry.kind}>
      {children}
    </Wrapper>
  );
}
