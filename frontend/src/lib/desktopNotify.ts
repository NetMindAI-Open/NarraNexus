/**
 * @file_name: desktopNotify.ts
 * @author: Bin Liang
 * @date: 2026-07-23
 * @description: OS-level notifications for the desktop (Tauri) build.
 *
 * The in-app completion toast is invisible when the user is in another
 * application (#44). On the desktop build we forward a system notification
 * through the Rust `notify_completion` command (tauri-plugin-notification)
 * via the same `__TAURI_INTERNALS__.invoke` channel every other desktop
 * bridge uses — NO `@tauri-apps/*` npm dependency (see lib/tauri.ts for
 * why the npm route breaks inside the packaged DMG). Web mode: no-op.
 */

import { isTauri, invokeTauri } from '@/lib/tauri';

/**
 * Notify that an agent finished its reply. Best-effort: failures (missing
 * OS permission, command not present in an older shell) are swallowed —
 * a notification must never break the chat flow.
 */
export async function notifyAgentReplyCompleted(agentName: string): Promise<void> {
  if (!isTauri()) return;
  try {
    await invokeTauri('notify_completion', {
      title: agentName,
      body: `${agentName} finished responding`,
    });
  } catch {
    /* best-effort */
  }
}
