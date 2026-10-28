/**
 * AgentCompletionToast — NM Design System (M3 Wave 5)
 *
 * Floating notification for work that finished while the user was looking
 * somewhere else. Auto-dismisses after 5 seconds; "View" goes to whatever it
 * is about — the completed AGENT, or the team ROOM that started talking.
 *
 * Rooms are here because the sidebar dot only answers "has anything happened"
 * when the user is looking at the sidebar, and a room is async precisely so
 * they can be elsewhere.
 *
 * NM treatment: NM Toast primitive (paper-raised + species color bar) with
 * silicon ring avatar for the agent identity. Inherits all NM motion + shape.
 */

import { useEffect, useCallback } from 'react';
import { Eye } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useChatStore, useConfigStore } from '@/stores';
import { useArtifactStore } from '@/stores/artifactStore';
import { toastKey, type ToastItem } from '@/stores/chatStore';
import { Toast, RingAvatar, GroupAvatar, Button } from '@/components/nm';

const AUTO_DISMISS_MS = 5000;

export function AgentCompletionToast() {
  const { t } = useTranslation();
  const navigate = useNavigate();
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
      return setTimeout(() => dismissToast(toastKey(toast)), remaining);
    });

    return () => timers.forEach(clearTimeout);
  }, [toastQueue, dismissToast]);

  // A team toast has no agent to switch to: it opens the ROOM. Routing rather
  // than selecting is the whole difference between the two kinds, so it lives
  // in one place instead of being inferred at each render.
  const handleView = useCallback(
    (toast: ToastItem) => {
      if (toast.kind === 'team') {
        navigate(`/app/teams/${toast.teamId}/chat`);
      } else if (toast.kind === 'artifact-repointed') {
        // Bring the repointed tab to front (restores if minimized) — the
        // whole point of the toast is "look at where it went".
        useArtifactStore.getState().restoreTab(toast.artifactId);
      } else {
        setAgentId(toast.agentId);
        setActiveAgent(toast.agentId);
      }
      dismissToast(toastKey(toast));
    },
    [navigate, setAgentId, setActiveAgent, dismissToast]
  );

  if (toastQueue.length === 0) return null;

  return (
    <div className="fixed bottom-6 right-6 z-[2000] flex flex-col gap-2">
      {toastQueue.map((toast) => {
        const name =
          toast.kind === 'team'
            ? toast.teamName || t('toast.room')
            : toast.kind === 'artifact-repointed'
              ? toast.title
              : toast.agentName || t('toast.agent');
        return (
          <div key={toastKey(toast)} className="animate-slide-in-right">
            <Toast
              status={toast.kind === 'artifact-repointed' ? 'warning' : 'success'}
              title={
                <span className="inline-flex items-center gap-2">
                  {toast.kind === 'team' ? (
                    /* A room is carbon+silicon, like its sidebar row — the
                       avatar is how the user tells the two kinds apart before
                       reading either line. */
                    <GroupAvatar
                      size="xs"
                      members={[{ species: 'carbon' }, { species: 'silicon' }]}
                      label={name.slice(0, 1)}
                    />
                  ) : toast.kind === 'artifact-repointed' ? null : (
                    <RingAvatar species="silicon" label={name.slice(0, 1)} size="xs" />
                  )}
                  <span>{name}</span>
                </span>
              }
              description={
                toast.kind === 'team'
                  ? t('toast.roomSpoke')
                  : toast.kind === 'artifact-repointed'
                    ? t(
                        toast.hashMatched
                          ? 'toast.artifactRepointedVerified'
                          : 'toast.artifactRepointedUnverified',
                        { oldPath: toast.oldPath, newPath: toast.newPath },
                      )
                    : t('toast.completed')
              }
              onDismiss={() => dismissToast(toastKey(toast))}
              action={
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleView(toast)}
                  leading={<Eye className="w-3 h-3" />}
                >
                  {t('toast.view')}
                </Button>
              }
            />
          </div>
        );
      })}
    </div>
  );
}
