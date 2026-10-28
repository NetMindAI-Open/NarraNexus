import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ForgotPasswordCard } from '../ForgotPasswordCard';

const sendResetCode = vi.fn();
const resetPassword = vi.fn();
vi.mock('@/lib/netmindAuth/useNetmindAuth', () => ({
  useNetmindAuth: () => ({ loading: false, error: '', sendResetCode, resetPassword }),
}));

afterEach(() => {
  sendResetCode.mockReset();
  resetPassword.mockReset();
});

describe('ForgotPasswordCard', () => {
  test('two-step: send code, then reset with code + new password', async () => {
    sendResetCode.mockResolvedValue(true);
    resetPassword.mockResolvedValue(true);
    render(<ForgotPasswordCard onClose={() => {}} />);

    // Step 1: email -> send code
    fireEvent.change(screen.getByPlaceholderText(/you@/i), {
      target: { value: 'a@b.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /send code/i }));
    await waitFor(() => expect(sendResetCode).toHaveBeenCalledWith('a@b.com'));

    // Step 2: code + a POLICY-VALID new password -> reset (fields appear only
    // after code sent). 'NewPw12!' satisfies length/upper/lower/digit/special.
    const codeInput = await screen.findByPlaceholderText(/verification code/i);
    fireEvent.change(codeInput, { target: { value: '123456' } });
    fireEvent.change(screen.getByPlaceholderText(/new password/i), {
      target: { value: 'NewPw12!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /reset password/i }));
    await waitFor(() =>
      expect(resetPassword).toHaveBeenCalledWith('a@b.com', '123456', 'NewPw12!'),
    );
  });

  test('does not advance to step 2 if sending the code fails', async () => {
    sendResetCode.mockResolvedValue(false);
    render(<ForgotPasswordCard onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/you@/i), {
      target: { value: 'a@b.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /send code/i }));
    await waitFor(() => expect(sendResetCode).toHaveBeenCalled());
    expect(screen.queryByPlaceholderText(/verification code/i)).toBeNull();
  });

  test('a policy-violating password cannot be submitted (no reset request sent)', async () => {
    sendResetCode.mockResolvedValue(true);
    render(<ForgotPasswordCard onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText(/you@/i), { target: { value: 'a@b.com' } });
    fireEvent.click(screen.getByRole('button', { name: /send code/i }));
    const codeInput = await screen.findByPlaceholderText(/verification code/i);
    fireEvent.change(codeInput, { target: { value: '123456' } });
    // 'weakpass' has no uppercase/digit/special — client policy must block it,
    // so resetPassword never runs and can't return a mask that hides the real cause.
    fireEvent.change(screen.getByPlaceholderText(/new password/i), { target: { value: 'weakpass' } });
    expect(screen.getByRole('button', { name: /reset password/i })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /reset password/i }));
    expect(resetPassword).not.toHaveBeenCalled();
  });
});
