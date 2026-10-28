/**
 * @file_name: ChatWidget.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The embeddable chat: history + streaming replies for one agent, styled with plain CSS
 * variables so a host page can theme it. No app store, no router — only `ChatClient`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ChatClient, type ChatClientOptions, type ChatTurnMessage } from './client';

export interface ChatWidgetProps extends ChatClientOptions {
  /** Placeholder of the composer. */
  placeholder?: string;
  /** Rendered height (CSS length). */
  height?: string;
  /** How many past events to load. */
  historyLimit?: number;
  /** Called whenever the message list changes (embedding hosts mirror it). */
  onMessages?: (messages: ChatTurnMessage[]) => void;
  className?: string;
}

const STYLE = `
.nx-chat{display:flex;flex-direction:column;border:1px solid var(--nx-border,#e5e7eb);border-radius:var(--nx-radius,12px);background:var(--nx-bg,#fff);color:var(--nx-fg,#111827);font:14px/1.5 var(--nx-font,system-ui,sans-serif);overflow:hidden}
.nx-chat__log{flex:1;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.nx-chat__msg{max-width:85%;padding:8px 12px;border-radius:12px;white-space:pre-wrap;word-break:break-word}
.nx-chat__msg--user{align-self:flex-end;background:var(--nx-user-bg,#2563eb);color:var(--nx-user-fg,#fff)}
.nx-chat__msg--assistant{align-self:flex-start;background:var(--nx-assistant-bg,#f3f4f6)}
.nx-chat__msg--error{align-self:center;color:var(--nx-error,#b91c1c);font-size:12px}
.nx-chat__msg--interrupted{align-self:center;color:var(--nx-error,#b91c1c);font-size:12px}
.nx-chat__form{display:flex;gap:8px;padding:8px;border-top:1px solid var(--nx-border,#e5e7eb)}
.nx-chat__input{flex:1;padding:8px 10px;border:1px solid var(--nx-border,#e5e7eb);border-radius:8px;font:inherit;background:transparent;color:inherit}
.nx-chat__btn{padding:8px 14px;border:0;border-radius:8px;background:var(--nx-user-bg,#2563eb);color:#fff;font:inherit;cursor:pointer}
.nx-chat__btn:disabled{opacity:.5;cursor:default}
`;

export function ChatWidget(props: ChatWidgetProps) {
  const { placeholder = 'Message…', height = '480px', historyLimit = 20, onMessages, className, ...clientOpts } = props;
  const client = useMemo(
    () => new ChatClient(clientOpts),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a new client only when the identity changes
    [clientOpts.baseUrl, clientOpts.agentId, clientOpts.userId, clientOpts.token, clientOpts.webSocket, clientOpts.fetch],
  );
  const [messages, setMessages] = useState<ChatTurnMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [interrupted, setInterrupted] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    client
      .history(historyLimit)
      .then((h) => alive && setMessages(h))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [client, historyLimit]);

  useEffect(() => {
    onMessages?.(messages);
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, onMessages]);

  const send = useCallback(() => {
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    setError(null);
    setInterrupted(false);
    setBusy(true);
    const now = Date.now();
    const assistantId = `local:${now}:assistant`;
    setMessages((m) => [
      ...m,
      { id: `local:${now}:user`, role: 'user', content: text, createdAt: now },
      { id: assistantId, role: 'assistant', content: '', createdAt: now },
    ]);
    const patch = (content: string) => setMessages((m) => m.map((x) => (x.id === assistantId ? { ...x, content } : x)));
    let acc = '';
    const handle = client.send(text, (ev) => {
      if (ev.type === 'delta') {
        acc += ev.text;
        patch(acc);
      } else if (ev.type === 'complete') {
        patch(ev.text || acc);
      } else if (ev.type === 'interrupted') {
        // The connection dropped mid-answer; the backend keeps running and will finish
        // writing the full answer server-side, but this socket never sees it. Show what
        // streamed in AND flag it as incomplete — never present a cut-off answer as done.
        patch(ev.text || acc);
        setInterrupted(true);
      } else if (ev.type === 'error') {
        setError(ev.message);
        if (!acc) setMessages((m) => m.filter((x) => x.id !== assistantId));
      } else if (ev.type === 'cancelled' && !acc) {
        setMessages((m) => m.filter((x) => x.id !== assistantId));
      }
    });
    stopRef.current = handle.stop;
    void handle.done.finally(() => {
      setBusy(false);
      stopRef.current = null;
    });
  }, [busy, client, draft]);

  return (
    <div className={`nx-chat${className ? ` ${className}` : ''}`} style={{ height }} data-agent-id={clientOpts.agentId}>
      <style>{STYLE}</style>
      <div className="nx-chat__log" ref={logRef} role="log" aria-live="polite">
        {messages.map((m) => (
          <div key={m.id} className={`nx-chat__msg nx-chat__msg--${m.role}`}>
            {m.content || (busy && m.role === 'assistant' ? '…' : '')}
          </div>
        ))}
        {error && <div className="nx-chat__msg nx-chat__msg--error">{error}</div>}
        {interrupted && (
          <div className="nx-chat__msg nx-chat__msg--interrupted">Connection interrupted — the reply above may be incomplete.</div>
        )}
      </div>
      <form
        className="nx-chat__form"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input className="nx-chat__input" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} aria-label="message" disabled={busy} />
        {busy ? (
          <button type="button" className="nx-chat__btn" onClick={() => stopRef.current?.()}>
            Stop
          </button>
        ) : (
          <button type="submit" className="nx-chat__btn" disabled={!draft.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}
