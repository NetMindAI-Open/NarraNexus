/**
 * @file_name: genericChannelConfig.test.tsx
 * @author: Bin Liang
 * @date: 2026-09-04
 * @description: The schema-driven channel panel renders the bind form from the schema (secrets as password inputs, required check), binds through the generic API, and once bound shows identity fields with test / deactivate / unbind.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { makeGenericChannelConfig } from '../genericChannelFactory';

const mocks = vi.hoisted(() => ({
  channelSchema: vi.fn(),
  channelCredential: vi.fn(),
  channelBind: vi.fn(),
  channelTest: vi.fn(),
  channelUnbind: vi.fn(),
  channelSetActive: vi.fn(),
}));
vi.mock('@/lib/api', () => ({ api: mocks }));
vi.mock('@/stores', () => ({ useConfigStore: (sel: (s: { agentId: string }) => unknown) => sel({ agentId: 'a1' }) }));

const fields = [
  { name: 'bot_token', kind: 'secret', label: 'Bot token', help: '', required: true, options: [], public: false },
  { name: 'bot_id', kind: 'string', label: 'Bot id', help: 'from the platform', required: false, options: [], public: true },
];
const schema = {
  channel: 'acme_chat', display_name: 'Acme Chat', transport: 'webhook', has_bind: true, has_test: false, manager_backed: false, external_id_field: 'bot_id',
  fields,
  bind_fields: fields, // a plugin channel binds with its stored shape
};

beforeEach(() => {
  mocks.channelSchema.mockResolvedValue({ success: true, data: schema });
  mocks.channelCredential.mockResolvedValue({ success: true, data: null });
  mocks.channelBind.mockResolvedValue({ success: true, data: { channel: 'acme_chat', agent_id: 'a1', enabled: true, external_id: 'b1', bot_id: 'b1' } });
  mocks.channelSetActive.mockResolvedValue({ success: true, enabled: false });
  mocks.channelUnbind.mockResolvedValue({ success: true });
});

describe('GenericChannelConfig', () => {
  it('renders the schema form, enforces required fields and binds', async () => {
    const Config = makeGenericChannelConfig('acme_chat');
    const changed = vi.fn();
    render(<Config onBindStateChange={changed} />);
    const token = (await screen.findByLabelText('Bot token')) as HTMLInputElement;
    expect(token.type).toBe('password');
    fireEvent.click(screen.getByText('Bind'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Bot token is required');
    fireEvent.change(token, { target: { value: 'tok' } });
    fireEvent.change(screen.getByLabelText('Bot id'), { target: { value: 'b1' } });
    mocks.channelCredential.mockResolvedValue({ success: true, data: { channel: 'acme_chat', agent_id: 'a1', enabled: true, external_id: 'b1', bot_id: 'b1' } });
    fireEvent.click(screen.getByText('Bind'));
    await waitFor(() => expect(mocks.channelBind).toHaveBeenCalledWith('acme_chat', 'a1', { bot_token: 'tok', bot_id: 'b1' }));
    expect(await screen.findByText('Bound to Acme Chat')).toBeInTheDocument();
    expect(screen.getByText('b1')).toBeInTheDocument();
    expect(screen.getByText(/Inbound webhook/)).toHaveTextContent('/api/channels/acme_chat/webhook/a1');
    expect(changed).toHaveBeenCalled();
    fireEvent.click(screen.getByText('Deactivate'));
    await waitFor(() => expect(mocks.channelSetActive).toHaveBeenCalledWith('acme_chat', 'a1', false));
    fireEvent.click(screen.getByText('Unbind'));
    await waitFor(() => expect(mocks.channelUnbind).toHaveBeenCalledWith('acme_chat', 'a1'));
  });
});
