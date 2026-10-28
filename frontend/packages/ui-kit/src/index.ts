/**
 * @file_name: index.ts
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: `@narranexus/ui-kit` — React components and the chat transport for embedding NarraNexus in another page.
 */
export { ChatWidget } from './ChatWidget';
export type { ChatWidgetProps } from './ChatWidget';
export { ChatClient, historyToMessages, wsUrl } from './client';
export type { ChatClientOptions, ChatTurnMessage, TurnEvent, TurnHandle } from './client';
