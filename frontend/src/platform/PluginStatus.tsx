/**
 * @file_name: PluginStatus.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: What a page/panel gate shows while its plugin is pending or after it failed to activate.
 *
 * Its own file so gates.tsx exports only gate factories (react-refresh's
 * only-export-components rule) — the shell's one loading/failure surface for
 * lazily activated plugin UI.
 */
export function PluginStatus({ pluginId, state, error }: { pluginId: string; state: string; error?: string }) {
  if (state === 'failed') {
    return (
      <div role="alert" className="p-4 text-sm text-[var(--color-error)]">
        Plugin {pluginId} failed to activate: {error}
      </div>
    );
  }
  return <div className="p-4 text-sm text-[var(--nm-ink50)]">Loading plugin {pluginId}…</div>;
}
