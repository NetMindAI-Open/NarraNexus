/**
 * @file_name: BrowserLoginNotice.tsx
 * @description: Open the live browser for a persisted login handoff without granting permissions.
 */
import { useId } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { LogIn, MonitorPlay } from 'lucide-react';
import { Button } from '@/components/nm/button';
import { useUIStore } from '@/stores/uiStore';
import type { BrowserLoginRequest } from '@/types/browser';

export default function BrowserLoginNotice({ request }: { request: BrowserLoginRequest }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const openPanel = useUIStore((state) => state.openPanel);
  const id = useId();

  return <div role="group" aria-labelledby={id} data-testid="browser-login-notice"
    className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--nm-hairline)] bg-[var(--nm-paper-warm)] p-4">
    <div className="flex min-w-0 items-start gap-2">
      <LogIn className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 space-y-1">
        <h3 id={id} className="text-sm font-medium">{t('browser.login.title', 'Login or verification requested')}</h3>
        <p className="break-words text-xs">{request.reason}</p>
        <p role="status" className="text-xs text-[var(--text-secondary)]">
          {request.state === 'in_control'
            ? t('browser.login.inControl', 'Return control in the browser when you are finished.')
            : t('browser.login.pending', 'Waiting for you to complete login or verification in the browser.')}
        </p>
      </div>
    </div>
    <Button variant="secondary" size="sm" leading={<MonitorPlay className="h-3.5 w-3.5" />}
      onClick={() => { openPanel('browser'); navigate('/app/chat'); }}>
      {t('browser.login.openBrowser', 'Open browser')}
    </Button>
  </div>;
}
