/** @file_name: useBrowserStream.ts
 * @description: Authenticated browser stream with session-scoped decoding and recovery.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { getAuthHeaders, getSessionToken } from '@/lib/authHeaders';
import { getWsBaseUrl } from '@/stores/runtimeStore';
import type { BrowserPage } from '@/types/browser';

/** Frame encodings the panel can paint. Frames name their own type (the
 *  server's SCREENCAST_MIME_TYPE), so a server-side format change shows up as
 *  a clear error here instead of an opaque decode failure. */
const FRAME_MIME_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

type Connection = 'connecting' | 'idle' | 'live' | 'reconnecting' | 'error';
interface StreamState {
  id: string | null | undefined;
  connection: Connection;
  holder: 'agent' | 'user';
  canControl: boolean;
  hasFrame: boolean;
  error: string | null;
  pages: BrowserPage[];
  selectedPageId: string | null;
  activePageId: string | null;
  followingActive: boolean;
  pageActionPending: boolean;
}
const initial = (id?: string | null): StreamState => ({
  id, connection: id ? 'connecting' : 'idle', holder: 'agent', canControl: false, hasFrame: false, error: null,
  pages: [], selectedPageId: null, activePageId: null, followingActive: true, pageActionPending: false,
});

export function useBrowserStream(sessionId: string | null | undefined, enabled: boolean) {
  const [state, setState] = useState<StreamState>(() => initial(sessionId));
  const [generation, setGeneration] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<{ width: number; height: number } | undefined>(undefined);
  const socketRef = useRef<WebSocket | null>(null);
  const controlRef = useRef(false);
  const pageRef = useRef<string | null>(null);
  const frameReadyRef = useRef(false);
  const switchingRef = useRef(false);
  const pageActionRef = useRef(false);
  const observeCanvasRef = useRef<((canvas: HTMLCanvasElement | null) => void) | null>(null);
  const attachCanvas = useCallback((canvas: HTMLCanvasElement | null) => {
    canvasRef.current = canvas;
    observeCanvasRef.current?.(canvas);
  }, []);

  useEffect(() => {
    if (!enabled || !sessionId) return;
    let disposed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let observer: ResizeObserver | undefined;
    let attempts = 0;
    let sequence = 0;
    let painted = 0;
    const update = (patch: Partial<StreamState>) => {
      if (!disposed) setState(previous => ({ ...(previous.id === sessionId ? previous : initial(sessionId)), ...patch }));
    };
    const connect = () => {
      if (disposed) return;
      const userId = getAuthHeaders()['X-User-Id'] || '';
      const socket = new WebSocket(`${getWsBaseUrl()}/ws/browser/${encodeURIComponent(sessionId)}?x_user_id=${encodeURIComponent(userId)}`);
      socketRef.current = socket;
      let lastMessage = Date.now();
      const current = () => !disposed && socketRef.current === socket;
      let live = false;
      let otherControl = false;
      let requestedSize = '';
      const resizeViewport = () => {
        if (!current() || !live || otherControl || !pageRef.current || socket.readyState !== WebSocket.OPEN) return;
        const bounds = canvasRef.current?.getBoundingClientRect();
        if (!bounds?.width || !bounds.height) return;
        const width = Math.min(1920, Math.max(240, Math.round(bounds.width)));
        const height = Math.min(1440, Math.max(240, Math.round(bounds.height)));
        const size = `${pageRef.current}:${width}:${height}`;
        if (size === requestedSize) return;
        requestedSize = size;
        socket.send(JSON.stringify({ type: 'resize', width, height, page_id: pageRef.current }));
      };
      observer = new ResizeObserver(resizeViewport);
      observeCanvasRef.current = canvas => {
        observer?.disconnect();
        if (canvas) observer?.observe(canvas);
        resizeViewport();
      };
      observeCanvasRef.current(canvasRef.current);
      controlRef.current = false;
      pageRef.current = null;
      frameReadyRef.current = false;
      switchingRef.current = false;
      pageActionRef.current = false;
      socket.onopen = () => {
        if (!current()) return;
        update({ ...initial(sessionId), connection: 'connecting' });
        socket.send(JSON.stringify({ type: 'auth', token: getSessionToken(), user_id: userId }));
        heartbeat = setInterval(() => {
          if (Date.now() - lastMessage > 45000) socket.close();
          else if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'ping' }));
        }, 15000);
      };
      socket.onmessage = event => {
        if (!current()) return;
        let message;
        try { message = JSON.parse(String(event.data)); } catch { return; }
        if (!message || typeof message !== 'object') return;
        lastMessage = Date.now();
        if (message.type === 'hello' || message.type === 'pages') {
          switchingRef.current = false;
          const selectedPageId = typeof message.selected_page_id === 'string' ? message.selected_page_id : null;
          if (pageRef.current !== selectedPageId || message.type === 'hello') {
            pageRef.current = selectedPageId;
            frameReadyRef.current = false;
            viewportRef.current = undefined;
            painted = ++sequence;
            update({ hasFrame: false });
          }
          update({ pages: Array.isArray(message.pages) ? message.pages : [], selectedPageId,
            activePageId: message.active_page_id ?? null, followingActive: message.following_active === true,
            error: message.page_error ?? null });
          if (message.type === 'pages') resizeViewport();
        }
        if (message.type === 'idle') {
          pageActionRef.current = false;
          live = false;
          attempts = 0;
          painted = ++sequence;
          controlRef.current = false;
          pageRef.current = null;
          frameReadyRef.current = false;
          update({ ...initial(sessionId), connection: 'idle' });
        } else if (message.type === 'hello' || message.type === 'control') {
          live = true;
          if (message.type === 'hello') requestedSize = '';
          attempts = 0;
          controlRef.current = message.control?.can_control === true;
          otherControl = message.control?.holder === 'user' && !controlRef.current;
          resizeViewport();
          update({ connection: 'live', error: null, holder: message.control?.holder === 'user' ? 'user' : 'agent', canControl: controlRef.current });
        } else if (message.type === 'page_action') {
          pageActionRef.current = false;
          update({ pageActionPending: false });
        } else if (message.type === 'error') {
          switchingRef.current = false;
          if (message.code === 'page_action_failed') {
            pageActionRef.current = false;
            update({ pageActionPending: false });
          }
          update({ error: typeof message.error === 'string' ? message.error : 'Browser stream failed' });
        } else if (message.type === 'frame' && typeof message.data === 'string') {
          if (message.page_id !== pageRef.current) return;
          if (typeof message.mime_type !== 'string' || !FRAME_MIME_TYPES.has(message.mime_type)) {
            update({ error: `Unsupported browser frame format: ${String(message.mime_type)}` });
            return;
          }
          const index = ++sequence;
          const image = new Image();
          image.onload = () => {
            if (!current() || index <= painted || message.page_id !== pageRef.current) return;
            const canvas = canvasRef.current;
            if (!canvas) return;
            painted = index;
            const { deviceWidth, deviceHeight } = message.meta ?? {};
            viewportRef.current = typeof deviceWidth === 'number' && Number.isFinite(deviceWidth) && deviceWidth > 0
              && typeof deviceHeight === 'number' && Number.isFinite(deviceHeight) && deviceHeight > 0
              ? { width: deviceWidth, height: deviceHeight } : { width: image.width, height: image.height };
            if (canvas.width !== image.width) canvas.width = image.width;
            if (canvas.height !== image.height) canvas.height = image.height;
            canvas.getContext('2d')?.drawImage(image, 0, 0);
            frameReadyRef.current = true;
            update({ hasFrame: true, connection: 'live' });
          };
          image.onerror = () => { if (current() && index > painted && message.page_id === pageRef.current) update({ error: 'Could not decode the browser frame' }); };
          image.src = `data:${message.mime_type};base64,${message.data}`;
        }
      };
      socket.onerror = () => { if (current()) socket.close(); };
      socket.onclose = event => {
        if (!current()) return;
        clearInterval(heartbeat);
        observer?.disconnect();
        controlRef.current = false;
        frameReadyRef.current = false;
        pageActionRef.current = false;
        painted = ++sequence;
        const forbidden = [1008, 4401, 4403].includes(event.code);
        update({ connection: forbidden ? 'error' : 'reconnecting', canControl: false, holder: 'agent', hasFrame: false, pageActionPending: false,
          error: forbidden ? 'Browser access was denied. Check your session and agent permissions.' : null });
        if (!forbidden) retry = setTimeout(connect, Math.min(1000 * 2 ** Math.min(attempts++, 4), 15000));
      };
    };
    connect();
    return () => {
      disposed = true;
      controlRef.current = false;
      frameReadyRef.current = false;
      pageRef.current = null;
      clearTimeout(retry);
      clearInterval(heartbeat);
      observer?.disconnect();
      observeCanvasRef.current = null;
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [sessionId, enabled, generation]);

  const send = useCallback((payload: Record<string, unknown>) => {
    const socket = socketRef.current;
    if (pageActionRef.current) return;
    if (payload.type === 'input' && (!controlRef.current || !frameReadyRef.current || switchingRef.current)) return;
    if (socket?.readyState !== WebSocket.OPEN) return;
    if (payload.type === 'new_page' || payload.type === 'navigate') {
      if (!controlRef.current || switchingRef.current) return;
      pageActionRef.current = true;
      setState(previous => ({ ...previous, pageActionPending: true, error: null }));
    }
    if (payload.type === 'select_page' || payload.type === 'follow_active') {
      switchingRef.current = true;
    }
    const pageScoped = payload.type === 'input' || payload.type === 'take_control';
    socket.send(JSON.stringify({ ...payload, ...(pageScoped ? { page_id: pageRef.current } : {}) }));
  }, []);
  const reconnect = useCallback(() => setGeneration(value => value + 1), []);
  return { ...(state.id === sessionId ? state : initial(sessionId)), canvasRef, attachCanvas, viewportRef, send, reconnect };
}
