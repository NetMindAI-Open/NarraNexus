/**
 * @file_name: index.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: `<narranexus-chat base-url agent-id user-id token height>` — the ChatWidget as a custom element for
 * pages that are not React. React and the widget are bundled in; attributes map to props and are live.
 */
import { createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';

import { ChatWidget, type ChatWidgetProps } from '@narranexus/ui-kit';

export const TAG = 'narranexus-chat';
const ATTRS = ['base-url', 'agent-id', 'user-id', 'token', 'height', 'placeholder', 'history-limit'] as const;

export class NarraNexusChatElement extends HTMLElement {
  static get observedAttributes(): string[] {
    return [...ATTRS];
  }

  private root: Root | null = null;

  connectedCallback(): void {
    this.render();
  }

  disconnectedCallback(): void {
    this.root?.unmount();
    this.root = null;
  }

  attributeChangedCallback(): void {
    if (this.isConnected) this.render();
  }

  /** The props the element currently renders (attributes → camelCase). */
  props(): ChatWidgetProps | null {
    const agentId = this.getAttribute('agent-id');
    const userId = this.getAttribute('user-id');
    if (!agentId || !userId) return null;
    const limit = this.getAttribute('history-limit');
    return {
      agentId,
      userId,
      baseUrl: this.getAttribute('base-url') ?? '',
      token: this.getAttribute('token') ?? undefined,
      height: this.getAttribute('height') ?? undefined,
      placeholder: this.getAttribute('placeholder') ?? undefined,
      historyLimit: limit ? Number(limit) : undefined,
    };
  }

  private render(): void {
    const props = this.props();
    if (!props) {
      this.textContent = `<${TAG}>: agent-id and user-id are required`;
      return;
    }
    this.root ??= createRoot(this);
    this.root.render(createElement(ChatWidget, props));
  }
}

export function register(tag: string = TAG): void {
  if (typeof customElements === 'undefined' || customElements.get(tag)) return;
  customElements.define(tag, NarraNexusChatElement);
}

register();
