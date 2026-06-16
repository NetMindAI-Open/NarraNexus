import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { AuthBindDialog } from '../AuthBindDialog';

describe('AuthBindDialog', () => {
  test('bandType 1 shows email + code inputs and submits them', () => {
    const onSubmit = vi.fn();
    render(
      <AuthBindDialog
        bindInfo={{ bandType: 1, identifyCode: 'x' }}
        loading={false}
        error=""
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'a@b.com' } });
    fireEvent.change(screen.getByLabelText(/code/i), { target: { value: '1234' } });
    fireEvent.click(screen.getByRole('button', { name: /confirm|bind|continue/i }));
    expect(onSubmit).toHaveBeenCalledWith({ email: 'a@b.com', verifyCode: '1234' });
  });

  test('bandType 3 shows confirm copy and submits with no extra', () => {
    const onSubmit = vi.fn();
    render(
      <AuthBindDialog
        bindInfo={{ bandType: 3, identifyCode: 'x', canBandEmail: 'me@x.com' }}
        loading={false}
        error=""
        onSubmit={onSubmit}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /confirm|bind|continue/i }));
    expect(onSubmit).toHaveBeenCalledWith({});
  });
});
