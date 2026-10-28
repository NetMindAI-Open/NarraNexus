/** @file_name: BrowserStreamPanel.regression.test.tsx
 * @description: Authenticated streaming, recovery and exclusive input regressions.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import BrowserStreamPanel, { type RuntimeStatus } from '../BrowserStreamPanel';
vi.mock('@/stores/runtimeStore', () => ({ getWsBaseUrl: () => 'ws://localhost:8000', getApiBaseUrl: () => '' }));
vi.mock('@/lib/authHeaders', () => ({ getAuthHeaders: () => ({ 'X-User-Id': 'user test' }), getSessionToken: () => 'session-token' }));
class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 1;
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  sent: Record<string, unknown>[] = [];
  constructor(public url: string) { FakeWebSocket.instances.push(this); }
  send(raw: string) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; }
  receive(message: Record<string, unknown>) {
    const defaults = message.type === 'hello' ? { pages: [{ id: 'main', title: 'Page', url: 'https://example.com', opener_id: null }],
      selected_page_id: 'main', active_page_id: 'main', following_active: true } : message.type === 'frame' ? { page_id: 'main' } : {};
    act(() => this.onmessage?.({ data: JSON.stringify({ ...defaults, ...message }) }));
  }
}
const images: FakeImage[] = [];
class FakeImage {
  width = 1280;
  height = 800;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  src = '';
  constructor() { images.push(this); }
}
const drawImage = vi.fn();
const ready: RuntimeStatus = { state: 'ready', reason: 'ready', version: 'Chromium', executable: '/chrome', progress: null };
const fetchReady = async () => ready;
beforeEach(() => {
  localStorage.setItem('narra-nexus-config', JSON.stringify({ state: { userId: 'user test', token: 'session-token' } }));
  FakeWebSocket.instances = [];
  images.length = 0;
  drawImage.mockClear();
  vi.stubGlobal('WebSocket', FakeWebSocket);
  vi.stubGlobal('Image', FakeImage);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage, clearRect: vi.fn() } as unknown as CanvasRenderingContext2D);
});
afterEach(() => { localStorage.removeItem('narra-nexus-config'); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function openPanel() {
  const view = render(<BrowserStreamPanel sessionId="agent-1" fetchStatus={fetchReady} />);
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const ws = FakeWebSocket.instances[0];
  act(() => ws.onopen?.());
  return { ...view, ws };
}
function hello(ws: FakeWebSocket, canControl = false, holder = 'agent') {
  ws.receive({ type: 'hello', control: { holder, can_control: canControl } });
  ws.receive({ type: 'frame', mime_type: 'image/jpeg', data: 'frame' });
  act(() => images.at(-1)?.onload?.());
}
describe('connection regressions', () => {
  test('a frame decodes by its own mime type and an unknown one says so', async () => {
    const { ws } = await openPanel();
    hello(ws);
    expect(images.at(-1)?.src.startsWith('data:image/jpeg;base64,')).toBe(true);
    ws.receive({ type: 'frame', mime_type: 'image/png', data: 'png-frame' });
    expect(images.at(-1)?.src).toBe('data:image/png;base64,png-frame');
    const decoded = images.length;
    ws.receive({ type: 'frame', mime_type: 'image/tiff', data: 'tiff-frame' });
    expect(images).toHaveLength(decoded);
    expect(await screen.findByText(/Unsupported browser frame format: image\/tiff/)).toBeInTheDocument();
  });
  test('lists popup tabs, switches viewing, and rejects old-page frame decoding', async () => {
    const { ws } = await openPanel();
    const pages = [
      { id: 'first', title: 'Search', url: 'https://example.com/search', opener_id: null },
      { id: 'popup', title: 'Sign in', url: 'https://login.example.com/', opener_id: 'first' },
    ];
    ws.receive({ type: 'hello', control: { holder: 'agent', can_control: false }, pages,
      selected_page_id: 'first', active_page_id: 'first', following_active: true });
    expect(screen.getAllByRole('tab')).toHaveLength(2);
    ws.receive({ type: 'frame', mime_type: 'image/jpeg', page_id: 'first', data: 'old' });
    fireEvent.click(screen.getByRole('tab', { name: /Sign in/ }));
    expect(ws.sent.at(-1)).toEqual({ type: 'select_page', page_id: 'popup' });
    ws.receive({ type: 'pages', pages, selected_page_id: 'popup', active_page_id: 'first', following_active: false });
    act(() => images.at(-1)?.onload?.());
    expect(drawImage).not.toHaveBeenCalled();
    ws.receive({ type: 'frame', mime_type: 'image/jpeg', page_id: 'first', data: 'late' });
    ws.receive({ type: 'frame', mime_type: 'image/jpeg', page_id: 'popup', data: 'new' });
    act(() => images.at(-1)?.onload?.());
    expect(drawImage).toHaveBeenCalledOnce();
    expect(screen.getByRole('tab', { name: /Sign in/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('textbox', { name: 'Web address' })).toHaveValue('https://login.example.com/');
    fireEvent.click(screen.getByRole('button', { name: 'Follow agent' }));
    expect(ws.sent.at(-1)).toEqual({ type: 'follow_active' });
  });
  test('status failure offers retry without claiming the runtime is absent', async () => {
    render(<BrowserStreamPanel fetchStatus={async () => { throw Error('service unavailable'); }} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('service unavailable');
    expect(screen.queryByTestId('browser-install-button')).toBeNull();
  });
  test('authenticates without putting the token in the URL', async () => {
    const { ws } = await openPanel();
    expect(ws.url).toContain('?x_user_id=user%20test');
    expect(ws.url).not.toContain('session-token');
    expect(ws.sent[0]).toEqual({ type: 'auth', token: 'session-token', user_id: 'user test' });
  });
  test('idle is visible and a later session starts without reopening', async () => {
    const { ws } = await openPanel();
    ws.receive({ type: 'idle' });
    expect(screen.getByTestId('browser-idle')).toBeTruthy();
    hello(ws);
    expect(screen.queryByTestId('browser-idle')).toBeNull();
    expect(drawImage).toHaveBeenCalledOnce();
  });
  test('the remote viewport follows the actual panel after hello and resize', async () => {
    let resized: (() => void) | undefined;
    vi.stubGlobal('ResizeObserver', class {
      constructor(private callback: () => void) {}
      observe(element: Element) { if (element instanceof HTMLCanvasElement) resized = this.callback; }
      disconnect() {}
    });
    const { ws } = await openPanel();
    const canvas = screen.getByTestId('browser-canvas');
    const measure = vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ width: 392, height: 617 } as DOMRect);
    hello(ws);
    expect(ws.sent).toContainEqual({ type: 'resize', width: 392, height: 617, page_id: 'main' });
    measure.mockReturnValue({ width: 600, height: 700 } as DOMRect);
    act(() => resized?.());
    expect(ws.sent.at(-1)).toEqual({ type: 'resize', width: 600, height: 700, page_id: 'main' });
  });
  test('observes a canvas mounted after a fast live-session hello', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 360, height: 640 } as DOMRect);
    const status = new Promise<RuntimeStatus>(() => {});
    render(<BrowserStreamPanel sessionId="agent-1" fetchStatus={() => status} />);
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0];
    hello(ws);
    expect(ws.sent).toContainEqual({ type: 'resize', width: 360, height: 640, page_id: 'main' });
  });
  test('disconnect releases control and reconnects', async () => {
    const { ws } = await openPanel();
    hello(ws, true, 'user');
    vi.useFakeTimers();
    act(() => ws.onclose?.({ code: 1006 }));
    expect(screen.getByTestId('browser-control-toggle')).toBeDisabled();
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(FakeWebSocket.instances).toHaveLength(2);
  });
  test('authorization failures stop automatic reconnection', async () => {
    const { ws } = await openPanel();
    vi.useFakeTimers();
    act(() => ws.onclose?.({ code: 4403 }));
    await act(async () => { vi.advanceTimersByTime(20000); });
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(screen.getByRole('alert')).toBeTruthy();
  });
  test('old-agent and out-of-order decoded frames cannot paint', async () => {
    const { ws, rerender } = await openPanel();
    ws.receive({ type: 'hello', control: { holder: 'agent', can_control: false } });
    ws.receive({ type: 'frame', mime_type: 'image/jpeg', data: 'first' });
    ws.receive({ type: 'frame', mime_type: 'image/jpeg', data: 'second' });
    act(() => images[1].onload?.());
    act(() => images[0].onload?.());
    expect(drawImage).toHaveBeenCalledTimes(1);
    ws.receive({ type: 'frame', mime_type: 'image/jpeg', data: 'old-agent' });
    rerender(<BrowserStreamPanel sessionId="agent-2" fetchStatus={fetchReady} />);
    act(() => images[2].onload?.());
    expect(drawImage).toHaveBeenCalledTimes(1);
    expect(ws.readyState).toBe(3);
  });
});
describe('exclusive input', () => {
  test('reveals the whole selected tab when takeover adds its close button', async () => {
    let resized: (() => void) | undefined;
    vi.stubGlobal('ResizeObserver', class {
      constructor(private callback: () => void) {}
      observe(element: Element) { if (element.getAttribute('role') === 'tablist') resized = this.callback; }
      disconnect() {}
    });
    const { ws } = await openPanel();
    hello(ws);
    const tab = screen.getByRole('tab');
    const reveal = vi.fn();
    tab.parentElement!.scrollIntoView = reveal;
    ws.receive({ type: 'control', control: { holder: 'user', can_control: true } });
    expect(reveal).toHaveBeenCalledWith({ block: 'nearest', inline: 'nearest' });
    reveal.mockClear();
    act(() => resized?.());
    expect(reveal).toHaveBeenCalledWith({ block: 'nearest', inline: 'nearest' });
  });

  test('owner creates a tab and navigates from its focused address bar', async () => {
    const { ws } = await openPanel();
    hello(ws);
    expect(screen.getByRole('button', { name: 'New tab' })).toBeDisabled();
    expect(screen.getByRole('textbox', { name: 'Web address' })).toHaveAttribute('readonly');
    ws.receive({ type: 'control', control: { holder: 'user', can_control: true } });
    fireEvent.click(screen.getByRole('button', { name: 'New tab' }));
    expect(ws.sent.at(-1)).toEqual({ type: 'new_page' });
    expect(screen.getByRole('button', { name: 'New tab' })).toBeDisabled();
    const pages = [
      { id: 'main', title: 'Page', url: 'https://example.com', opener_id: null },
      { id: 'manual', title: '', url: 'about:blank', opener_id: null },
    ];
    ws.receive({ type: 'pages', pages, selected_page_id: 'manual', active_page_id: 'manual', following_active: true });
    ws.receive({ type: 'page_action', action: 'new_page', page_id: 'manual' });
    const address = screen.getByRole('textbox', { name: 'Web address' });
    expect(address).toHaveFocus();
    fireEvent.change(address, { target: { value: 'example.org/articles' } });
    fireEvent.submit(address.closest('form')!);
    expect(ws.sent.at(-1)).toEqual({ type: 'navigate', page_id: 'manual', url: 'https://example.org/articles' });
    expect(screen.getByRole('button', { name: 'Go' })).toBeDisabled();
    ws.receive({ type: 'error', code: 'page_action_failed', error: 'net::ERR_NAME_NOT_RESOLVED' });
    expect(screen.getByRole('alert')).toHaveTextContent('net::ERR_NAME_NOT_RESOLVED');
    expect(screen.getByRole('button', { name: 'Go' })).not.toBeDisabled();
    fireEvent.change(address, { target: { value: 'javascript:alert(1)' } });
    const count = ws.sent.length;
    fireEvent.submit(address.closest('form')!);
    expect(ws.sent).toHaveLength(count);
    expect(address).toHaveAttribute('aria-invalid', 'true');
    ws.receive({ type: 'control', control: { holder: 'agent', can_control: false } });
    expect(address).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'Close tab' })).toBeNull();
  });

  test('uses the painted frame metadata when JPEG size differs from page coordinates', async () => {
    const { ws } = await openPanel();
    ws.receive({ type: 'hello', control: { holder: 'user', can_control: true } });
    ws.receive({ type: 'frame', mime_type: 'image/jpeg', data: 'frame', meta: { deviceWidth: 640, deviceHeight: 400 } });
    act(() => images.at(-1)?.onload?.());
    const canvas = screen.getByTestId('browser-canvas');
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 640, height: 400 } as DOMRect);
    fireEvent.pointerDown(canvas, { clientX: 320, clientY: 200, button: 0 });
    expect(ws.sent.at(-1)).toMatchObject({ type: 'input', event: { x: 320, y: 200 } });
  });
  test('spectators cannot send input when another client owns control', async () => {
    const { ws } = await openPanel();
    hello(ws, false, 'user');
    const canvas = screen.getByTestId('browser-canvas');
    fireEvent.pointerDown(canvas, { clientX: 10, clientY: 10 });
    fireEvent.keyDown(canvas, { key: 'a' });
    fireEvent.wheel(canvas, { deltaY: 100 });
    expect(ws.sent.filter(m => m.type === 'input')).toHaveLength(0);
    expect(screen.getByTestId('browser-holder')).toHaveTextContent('Another window');
    expect(screen.getByTestId('browser-control-toggle')).toBeDisabled();
  });
  test('takeover is explicit and requires server confirmation', async () => {
    const { ws } = await openPanel();
    hello(ws);
    fireEvent.click(screen.getByTestId('browser-control-toggle'));
    expect(ws.sent.at(-1)).toEqual({ type: 'take_control', page_id: 'main' });
    fireEvent.keyDown(screen.getByTestId('browser-canvas'), { key: 'a' });
    expect(ws.sent.filter(m => m.type === 'input')).toHaveLength(0);
    ws.receive({ type: 'control', control: { holder: 'user', can_control: true } });
    fireEvent.keyDown(screen.getByTestId('browser-canvas'), { key: 'a', code: 'KeyA', keyCode: 65 });
    expect(ws.sent.at(-1)).toMatchObject({ type: 'input', event: { kind: 'key', key: 'a', text: 'a', code: 'KeyA' } });
  });
  test('paste and composed text are inserted once', async () => {
    const { ws } = await openPanel();
    hello(ws, true, 'user');
    const input = screen.getByLabelText('Browser keyboard input');
    fireEvent.paste(input, { clipboardData: { getData: () => 'pasted text' } });
    fireEvent.compositionStart(input);
    fireEvent.compositionEnd(input, { data: '\u4e2d\u6587' });
    expect(ws.sent.filter(m => m.type === 'input')).toEqual([
      { type: 'input', page_id: 'main', event: { kind: 'text', text: 'pasted text' } },
      { type: 'input', page_id: 'main', event: { kind: 'text', text: '\u4e2d\u6587' } },
    ]);
  });
  test('a drag released in letterboxing still releases the remote button', async () => {
    const { ws } = await openPanel();
    hello(ws, true, 'user');
    const canvas = screen.getByTestId('browser-canvas');
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 400, height: 600 } as DOMRect);
    fireEvent(canvas, new MouseEvent('pointerdown', { bubbles: true, clientX: 200, clientY: 300, button: 0, buttons: 1 }));
    fireEvent(canvas, new MouseEvent('pointerup', { bubbles: true, clientX: 200, clientY: 10, button: 0, buttons: 0 }));
    expect(ws.sent.filter(m => m.type === 'input')).toMatchObject([
      { event: { type: 'mousePressed', x: 640, y: 400 } },
      { event: { type: 'mouseReleased', x: 640, y: 400 } },
    ]);
  });
  test('touch dragging scrolls without pressing a remote mouse button', async () => {
    const { ws } = await openPanel();
    hello(ws, true, 'user');
    const canvas = screen.getByTestId('browser-canvas');
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 400, height: 600 } as DOMRect);
    const touch = (type: string, y: number) => fireEvent(canvas, Object.assign(new MouseEvent(type, { bubbles: true, clientX: 200, clientY: y }), { pointerType: 'touch', pointerId: 1 }));
    touch('pointerdown', 300);
    touch('pointermove', 270);
    touch('pointerup', 270);
    expect(ws.sent.filter(m => m.type === 'input')).toMatchObject([{ event: { kind: 'wheel', deltaY: 96 } }]);
  });
});
