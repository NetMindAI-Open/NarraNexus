/**
 * @file_name: element.test.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: <narranexus-chat> registers once, maps attributes to ChatWidget props, renders the widget when connected and complains when the required attributes are missing.
 */
import { describe, expect, it } from 'vitest';

import { NarraNexusChatElement, TAG, register } from '../index';

describe('<narranexus-chat>', () => {
  it('registers once and maps attributes to props', () => {
    register();
    register();
    expect(customElements.get(TAG)).toBe(NarraNexusChatElement);
    const el = document.createElement(TAG) as NarraNexusChatElement;
    el.setAttribute('agent-id', 'a1');
    el.setAttribute('user-id', 'u1');
    el.setAttribute('base-url', 'http://x');
    el.setAttribute('history-limit', '3');
    expect(el.props()).toEqual({ agentId: 'a1', userId: 'u1', baseUrl: 'http://x', token: undefined, height: undefined, placeholder: undefined, historyLimit: 3 });
  });

  it('renders the widget when connected and a hint when attributes are missing', async () => {
    const bad = document.createElement(TAG) as NarraNexusChatElement;
    document.body.appendChild(bad);
    expect(bad.textContent).toContain('agent-id and user-id are required');
    const el = document.createElement(TAG) as NarraNexusChatElement;
    el.setAttribute('agent-id', 'a1');
    el.setAttribute('user-id', 'u1');
    el.setAttribute('base-url', 'http://x');
    document.body.appendChild(el);
    await new Promise((r) => setTimeout(r, 0));
    expect(el.querySelector('.nx-chat')).not.toBeNull();
    expect(el.querySelector('.nx-chat')?.getAttribute('data-agent-id')).toBe('a1');
    el.remove();
  });
});
