/**
 * AgentCompletionToast - Floating notification when background agents complete
 *
 * Displays in the bottom-right corner, auto-dismisses after 5 seconds.
 * Click "View" to switch to the completed agent.
 */

import { useEffect, useCallback } from 'react';
import { Bot, X, Eye } from 'lucide-react';
import { useChatStore, useConfigStore } from '@/stores';
import { cn } from '@/lib/utils';

const AUTO_DISMISS_MS = 5000;

export function AgentCompletionToast() {
  const toastQueue = useChatStore((s) => s.toastQueue);
  const dismissToast = useChatStore((s) => s.dismissToast);
  const setActiveAgent = useChatStore((s) => s.setActiveAgent);
  const setAgentId = useConfigStore((s) => s.setAgentId);

  // Auto-dismiss toasts after timeout
  useEffect(() => {
    if (toastQueue.length === 0) return;

    const timers = toastQueue.map((toast) => {
      const elapsed = Date.now() - toast.timestamp;
      const remaining = Math.max(AUTO_DISMISS_MS - elapsed, 0);
      return setTimeout(() => dismissToast(toast.agentId), remaining);
    });

    return () => timers.forEach(clearTimeout);
  }, [toastQueue, dismissToast]);

  const handleView = useCallback((agentId: string) => {
    setAgentId(agentId);
    setActiveAgent(agentId);
    dismissToast(agentId);
  }, [setAgentId, setActiveAgent, dismissToast]);

  if (toastQueue.length === 0) return null;

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2">
      {toastQueue.map((toast) => (
        <div
          key={toast.agentId}
          className={cn(
            'flex items-center gap-3 px-4 py-3 rounded-xl',
            'bg-[var(--bg-secondary)] border border-[var(--accent-primary)]/30',
            'shadow-[0_4px_24px_rgba(0,0,0,0.3),0_0_20px_var(--accent-glow)]',
            'animate-slide-up',
            'min-w-[280px] max-w-[360px]',
          )}
        >
          <div className="w-8 h-8 rounded-lg bg-[var(--accent-primary)]/15 flex items-center justify-center shrink-0">
            <Bot className="w-4 h-4 text-[var(--accent-primary)]" />
          </div>

          <div className="flex-1 min-w-0">
            <p className="text-sm text-[var(--text-primary)] font-medium truncate">
              {toast.agentName}
            </p>
            <p className="text-[10px] text-[var(--text-tertiary)]">Completed</p>
          </div>

          <button
            onClick={() => handleView(toast.agentId)}
            className="shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium text-[var(--accent-primary)] bg-[var(--accent-primary)]/10 hover:bg-[var(--accent-primary)]/20 transition-colors"
          >
            <Eye className="w-3 h-3" />
            View
          </button>

          <button
            onClick={() => dismissToast(toast.agentId)}
            className="shrink-0 p-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <X className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          </button>
        </div>
      ))}
    </div>
  );
}
