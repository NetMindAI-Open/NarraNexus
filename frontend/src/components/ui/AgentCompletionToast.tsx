/**
 * AgentCompletionToast — NM Design System (M3 Wave 5)
 *
 * Floating notification when background agents complete. Auto-dismisses
 * after 5 seconds. Click "View" to switch to the completed agent.
 *
 * NM treatment: NM Toast primitive (paper-raised + species color bar) with
 * silicon ring avatar for the agent identity. Inherits all NM motion + shape.
 */

import { useEffect, useCallback } from 'react';
import { Eye } from 'lucide-react';
import { useChatStore, useConfigStore } from '@/stores';
import { Toast, RingAvatar, Button } from '@/components/nm';

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

  const handleView = useCallback(
    (agentId: string) => {
      setAgentId(agentId);
      setActiveAgent(agentId);
      dismissToast(agentId);
    },
    [setAgentId, setActiveAgent, dismissToast]
  );

  if (toastQueue.length === 0) return null;

  return (
    <div className="fixed bottom-6 right-6 z-[2000] flex flex-col gap-2">
      {toastQueue.map((toast) => (
        <div key={toast.agentId} className="animate-slide-in-right">
          <Toast
            status="success"
            title={
              <span className="inline-flex items-center gap-2">
                <RingAvatar
                  species="silicon"
                  label={(toast.agentName || 'A').slice(0, 1)}
                  size="xs"
                />
                <span>{toast.agentName || 'Agent'}</span>
              </span>
            }
            description="Completed"
            onDismiss={() => dismissToast(toast.agentId)}
            action={
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handleView(toast.agentId)}
                leading={<Eye className="w-3 h-3" />}
              >
                View
              </Button>
            }
          />
        </div>
      ))}
    </div>
  );
}
