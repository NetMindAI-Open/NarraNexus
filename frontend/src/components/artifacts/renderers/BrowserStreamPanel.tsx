/** @file_name: BrowserStreamPanel.tsx
 * @date: 2026-09-22
 * @description: Watch the agent's browser and explicitly take over its input.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Download, Hand, Loader2, MonitorPlay, RefreshCw, X } from 'lucide-react';
import { Button } from '@/components/nm/button';
import { api } from '@/lib/api';
import type { BrowserRuntimeStatus } from '@/types/browser';
import { inputModifiers, remotePoint } from './browserInput';
import { useBrowserStream } from './useBrowserStream';
import { BrowserPageTabs } from './BrowserPageTabs';

export type RuntimeStatus = BrowserRuntimeStatus;
interface Props {
  sessionId?: string | null;
  fetchStatus?: () => Promise<RuntimeStatus>;
  startInstall?: () => Promise<{ ok: boolean; error: string | null }>;
}
const defaultFetchStatus = () => api.getBrowserRuntime();
const defaultStartInstall = () => api.installBrowserRuntime();

export default function BrowserStreamPanel({ sessionId, fetchStatus = defaultFetchStatus, startInstall = defaultStartInstall }: Props) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<RuntimeStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [installError, setInstallError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const keyboardRef = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  const pressedAt = useRef<{ x: number; y: number } | null>(null);
  const touchGesture = useRef<{ start: { x: number; y: number }; last: { x: number; y: number }; moved: boolean } | null>(null);
  const stream = useBrowserStream(sessionId, true);
  const { canvasRef, viewportRef, send, canControl } = stream;

  // Serial polling also observes an install started in Settings or another window.
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await fetchStatus();
        if (!active) return;
        setStatus(next);
        setLoadError(null);
        timer = setTimeout(poll, next.state === 'installing' ? 1000 : 5000);
      } catch (error) {
        if (!active) return;
        setLoadError(error instanceof Error ? error.message : String(error));
        timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [fetchStatus, refreshKey]);

  const input = useCallback((event: Record<string, unknown>) => send({ type: 'input', event }), [send]);
  const point = useCallback((event: { clientX: number; clientY: number }) => {
    const canvas = canvasRef.current;
    return canvas && remotePoint(canvas.getBoundingClientRect(), canvas.width, canvas.height, event.clientX, event.clientY, viewportRef.current);
  }, [canvasRef, viewportRef]);

  // A native non-passive listener prevents scrolling the surrounding app during takeover.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !canControl) return;
    const wheel = (event: WheelEvent) => {
      const position = point(event);
      if (!position) return;
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.height : 1;
      input({ kind: 'wheel', ...position, deltaX: event.deltaX * unit, deltaY: event.deltaY * unit, modifiers: inputModifiers(event) });
    };
    canvas.addEventListener('wheel', wheel, { passive: false });
    return () => canvas.removeEventListener('wheel', wheel);
  }, [canControl, canvasRef, point, input]);

  const pointer = (event: PointerEvent<HTMLCanvasElement>, type: string) => {
    if (!canControl || !stream.hasFrame) return;
    const position = point(event) ?? (type === 'mouseReleased' ? pressedAt.current : null);
    if (!position) return;
    event.preventDefault();
    if (type === 'mousePressed') {
      pressedAt.current = position;
      event.currentTarget.setPointerCapture?.(event.pointerId);
      if (event.pointerType !== 'touch') keyboardRef.current?.focus({ preventScroll: true });
    }
    if (event.pointerType === 'touch') {
      if (type === 'mousePressed') touchGesture.current = { start: position, last: position, moved: false };
      const gesture = touchGesture.current;
      if (!gesture) return;
      if (type === 'mouseMoved') {
        gesture.moved ||= Math.hypot(position.x - gesture.start.x, position.y - gesture.start.y) > 12;
        if (gesture.moved) input({ kind: 'wheel', ...position, deltaX: gesture.last.x - position.x, deltaY: gesture.last.y - position.y });
        gesture.last = position;
      } else if (type === 'mouseReleased') {
        if (!gesture.moved && event.type !== 'pointercancel') {
          keyboardRef.current?.focus({ preventScroll: true });
          input({ kind: 'mouse', type: 'mousePressed', ...position, button: 'left', buttons: 1, clickCount: 1 });
          input({ kind: 'mouse', type: 'mouseReleased', ...position, button: 'left', buttons: 0, clickCount: 1 });
        }
        touchGesture.current = null;
        pressedAt.current = null;
      }
      return;
    }
    input({ kind: 'mouse', type, ...position, button: ['left', 'middle', 'right'][event.button] ?? 'none', buttons: event.buttons, clickCount: type === 'mouseMoved' ? 0 : Math.max(1, event.detail), modifiers: inputModifiers(event) });
    if (type === 'mouseReleased') pressedAt.current = null;
  };

  const key = (event: KeyboardEvent, type: 'keyDown' | 'keyUp') => {
    if (!canControl || composing.current || event.nativeEvent.isComposing || event.key === 'Process' || event.keyCode === 229) return;
    // Let the browser deliver paste through ClipboardEvent, including its actual text.
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') return;
    event.preventDefault();
    const text = type === 'keyDown' && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey ? event.key : undefined;
    input({ kind: 'key', type, key: event.key, code: event.code, windowsVirtualKeyCode: event.keyCode, modifiers: inputModifiers(event), ...(text ? { text } : {}) });
  };

  const install = async (cancel = false) => {
    if (busy) return;
    setBusy(true);
    setInstallError(null);
    try {
      const result = cancel ? await api.cancelBrowserInstall() : await startInstall();
      if (!result?.ok) throw Error(('error' in result && typeof result.error === 'string' && result.error) || t('browser.installFailed', 'The browser installation request failed.'));
    } catch (error) {
      setInstallError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
      setRefreshKey(value => value + 1);
    }
  };

  const liveSession = stream.connection === 'live';
  if (loadError && !status && !liveSession) return <div className="flex h-full flex-col items-center justify-center gap-3 p-4">
    <div role="alert" className="break-words text-xs text-[var(--color-error)]">{loadError}</div>
    <Button size="sm" variant="secondary" leading={<RefreshCw className="h-4 w-4" />} onClick={() => setRefreshKey(value => value + 1)}>{t('settings.browser.recheck', 'Check again')}</Button>
  </div>;
  if (!status && !liveSession) return <div className="flex h-full items-center justify-center" data-testid="browser-loading"><Loader2 className="h-4 w-4 animate-spin" /></div>;
  if (status && status.state !== 'ready' && !liveSession) {
    const installing = status.state === 'installing';
    const systemUnavailable = status.selection?.source === 'system' && !installing;
    const percent = status.progress?.percent;
    return <div className="flex h-full flex-col items-center justify-center gap-3 p-4 text-center" data-testid="browser-install-card">
      <MonitorPlay className="h-6 w-6 opacity-60" />
      <div className="text-sm">{systemUnavailable
        ? t('settings.browser.systemUnavailable', 'Google Chrome is unavailable. Install Google Chrome or select the managed browser.')
        : status.reason === 'probe-failed' ? t('browser.brokenTitle', 'The browser is installed but will not start') : t('browser.absentTitle', 'Browser not installed')}</div>
      {installing ? <div className="w-full max-w-56" data-testid="browser-install-progress">
        <div role="progressbar" aria-label={t('browser.installingUnknown', 'Downloading...')} aria-valuenow={percent ?? undefined} className="h-1 overflow-hidden rounded-[var(--radius-sm)] bg-[var(--nm-hairline)]">
          <div className={`h-full bg-[var(--nm-ink)] ${percent == null ? 'animate-pulse' : ''}`} style={{ width: percent == null ? '100%' : `${Math.min(100, Math.max(0, percent))}%` }} />
        </div>
        <div className="mt-2 text-xs">{percent == null ? t('browser.installingUnknown', 'Downloading...') : `${percent}%`}</div>
        <Button className="mt-2" variant="ghost" size="sm" leading={<X className="h-4 w-4" />} disabled={busy} onClick={() => void install(true)}>{t('browser.cancelInstall', 'Cancel download')}</Button>
      </div> : systemUnavailable ? <Button variant="secondary" size="sm" leading={<RefreshCw className="h-4 w-4" />} onClick={() => setRefreshKey(value => value + 1)}>
        {t('settings.browser.recheck', 'Check again')}
      </Button> : <Button variant="secondary" size="sm" data-testid="browser-install-button" disabled={busy} leading={<Download className="h-4 w-4" />} onClick={() => void install()}>
        {status.reason === 'probe-failed' ? t('browser.reinstall', 'Re-install browser') : t('browser.install', 'Install browser')}
      </Button>}
      {(installError || loadError) && <div role="alert" data-testid="browser-install-error" className="max-w-full break-words text-xs text-[var(--color-error)]">{installError || loadError}</div>}
    </div>;
  }

  const waiting = stream.connection !== 'live' || !stream.hasFrame;
  const idle = !sessionId || stream.connection === 'idle';
  return <div className="flex h-full min-h-0 min-w-0 flex-col" data-testid="browser-panel">
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--nm-hairline)] p-2 text-xs">
      <span className="min-w-0 flex-1" data-testid="browser-holder">
        {canControl ? t('browser.youDriving', 'You are driving') : stream.holder === 'user' ? t('browser.otherDriving', 'Another window is driving') : t('browser.agentDriving', 'Agent is driving')}
      </span>
      <Button size="sm" variant="ghost" leading={<Hand className="h-3.5 w-3.5" />} data-testid="browser-control-toggle"
        disabled={waiting || stream.pageActionPending || (stream.holder === 'user' && !canControl)} onClick={() => send({ type: canControl ? 'release_control' : 'take_control' })}>
        {canControl ? t('browser.handBack', 'Hand back to agent') : t('browser.takeControl', 'Take control')}
      </Button>
    </div>
    <BrowserPageTabs pages={stream.pages} selectedPageId={stream.selectedPageId} activePageId={stream.activePageId}
      followingActive={stream.followingActive} canControl={canControl} busy={stream.pageActionPending} send={send} />
    {(stream.error || loadError) && <div role="alert" className="flex flex-wrap items-center gap-2 border-b border-[var(--nm-hairline)] p-2 text-xs text-[var(--color-error)]">
      <span className="min-w-0 flex-1 break-words">{stream.error || loadError}</span>
      <Button size="sm" variant="ghost" leading={<RefreshCw className="h-3.5 w-3.5" />} onClick={stream.reconnect}>{t('browser.reconnect', 'Reconnect')}</Button>
    </div>}
    <div className="relative min-h-0 flex-1 overflow-hidden bg-[var(--bg-deep)]" onKeyDown={event => key(event, 'keyDown')} onKeyUp={event => key(event, 'keyUp')}>
      <canvas ref={stream.attachCanvas} tabIndex={canControl ? 0 : -1} aria-label={t('browser.screen', 'Browser screen')} data-testid="browser-canvas"
        className={`absolute inset-0 h-full w-full object-contain ${waiting ? 'invisible' : ''} ${canControl ? 'touch-none' : ''}`}
        onPointerDown={event => pointer(event, 'mousePressed')} onPointerUp={event => pointer(event, 'mouseReleased')}
        onPointerMove={event => pointer(event, 'mouseMoved')} onPointerCancel={event => pointer(event, 'mouseReleased')}
        onContextMenu={event => { if (canControl) event.preventDefault(); }} />
      <textarea ref={keyboardRef} className="sr-only" tabIndex={-1} aria-label={t('browser.keyboardInput', 'Browser keyboard input')} disabled={!canControl}
        autoCapitalize="off" autoCorrect="off" spellCheck={false}
        onPaste={event => { if (canControl) { event.preventDefault(); input({ kind: 'text', text: event.clipboardData.getData('text/plain') }); } }}
        onCompositionStart={() => { composing.current = true; }}
        onCompositionEnd={event => { composing.current = false; if (canControl && event.data) input({ kind: 'text', text: event.data }); event.currentTarget.value = ''; }}
        onInput={event => { if (!composing.current && event.currentTarget.value) { input({ kind: 'text', text: event.currentTarget.value }); event.currentTarget.value = ''; } }} />
      {waiting && <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center text-xs opacity-70" data-testid={idle ? 'browser-idle' : 'browser-connecting'}>
        {idle ? <MonitorPlay className="h-5 w-5" /> : <Loader2 className="h-5 w-5 animate-spin" />}
        <span>{idle ? t('browser.idle', 'No live browser session yet.') : stream.connection === 'reconnecting' ? t('browser.reconnecting', 'Reconnecting...') : stream.connection === 'error' ? t('browser.disconnected', 'Disconnected') : t('browser.connecting', 'Connecting...')}</span>
      </div>}
    </div>
  </div>;
}
