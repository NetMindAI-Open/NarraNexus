/**
 * @file_name: BrowserManualInstall.tsx
 * @description: Copy the running installer's manual fallback without constructing shell commands.
 */
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy } from 'lucide-react';
import { Button } from '@/components/nm/button';
import { FormField, Select, Textarea } from '@/components/nm/form';

interface Props {
  command: string | null;
  statusCommand?: string;
  cancelCommand?: string;
  root?: string;
  shell?: 'posix' | 'powershell';
}

export default function BrowserManualInstall({ command, statusCommand, cancelCommand, root, shell }: Props) {
  const { t } = useTranslation();
  const [action, setAction] = useState('install');
  if (!command?.trim()) return null;
  const options = [
    { value: 'install', label: t('settings.browser.install', 'Install browser'), command },
    ...(statusCommand ? [{ value: 'status', label: t('settings.browser.recheck', 'Check again'), command: statusCommand }] : []),
    ...(cancelCommand ? [{ value: 'cancel', label: t('settings.browser.cancel', 'Cancel installation'), command: cancelCommand }] : []),
  ];
  const selected = options.find((option) => option.value === action) ?? options[0];
  return <div className="min-w-0 space-y-2 border-t border-[var(--nm-hairline)] pt-4">
    <p className="text-xs text-[var(--text-secondary)]">{t('settings.browser.manualHost', 'Run on the computer hosting NarraNexus.')}</p>
    {root && <p className="break-all text-xs text-[var(--text-secondary)]">
      {t('settings.browser.manualRoot', 'Installation directory')}: <span>{root}</span>
    </p>}
    {shell && <p className="text-xs text-[var(--text-secondary)]">
      {t('settings.browser.manualShell', 'Shell')}: {shell === 'powershell' ? 'PowerShell' : 'POSIX'}
    </p>}
    {options.length > 1 && <FormField label={t('settings.browser.manualCommand', 'Command')}>
      <Select value={selected.value} options={options} onChange={(event) => setAction(event.target.value)} />
    </FormField>}
    <ManualCommand key={selected.command} command={selected.command} />
  </div>;
}

function ManualCommand({ command }: { command: string }) {
  const { t } = useTranslation();
  const input = useRef<HTMLTextAreaElement>(null);
  const copying = useRef(false);
  const [state, setState] = useState<'idle' | 'copying' | 'copied' | 'failed'>('idle');

  const copy = async () => {
    if (copying.current) return;
    copying.current = true;
    setState('copying');
    try {
      await navigator.clipboard.writeText(command);
      setState('copied');
    } catch {
      setState('failed');
      input.current?.focus();
      input.current?.select();
    } finally {
      copying.current = false;
    }
  };

  return <div className="min-w-0 space-y-2">
    <FormField label={t('settings.browser.manualInstall', 'Manual installation')}>
      <Textarea ref={input} value={command} readOnly rows={3}
        className="font-mono text-xs break-all" />
    </FormField>
    <Button variant="secondary" size="sm" leading={<Copy className="h-3.5 w-3.5" />}
      loading={state === 'copying'} onClick={() => void copy()}>
      {t('settings.browser.copyCommand', 'Copy command')}
    </Button>
    {state === 'copied' && <p role="status" className="text-xs text-[var(--text-secondary)]">
      {t('settings.browser.commandCopied', 'Command copied.')}
    </p>}
    {state === 'failed' && <p role="alert" className="text-xs text-[var(--color-error)]">
      {t('settings.browser.copyFailed', 'Could not copy. The command is selected for manual copying.')}
    </p>}
  </div>;
}
