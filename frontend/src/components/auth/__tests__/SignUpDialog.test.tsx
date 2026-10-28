/**
 * @file_name: SignUpDialog.test.tsx
 * @description: Signup form behaviour.
 *
 * The first version of this dialog filtered the verification-code field with
 * `replace(/\D/g, '')` — a guess that the "6-digit code" in the spec meant
 * digits. It does not: the codes are alphanumeric, so every letter a user typed
 * or pasted was silently deleted and the form could never be completed. These
 * tests pin the input rules that guess broke.
 */
import { expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const mockSendCode = vi.fn();
const mockSignup = vi.fn();
const mockReportFunnel = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    sendSignupCode: (...a: unknown[]) => mockSendCode(...a),
    signup: (...a: unknown[]) => mockSignup(...a),
    reportAuthFunnel: (...a: unknown[]) => mockReportFunnel(...a),
  },
}));

import { SignUpDialog } from '../SignUpDialog';

const codeField = () =>
  screen.getByLabelText(/pages.signup.code/i) as HTMLInputElement;

beforeEach(() => {
  mockSendCode.mockReset().mockResolvedValue({ success: true });
  mockSignup.mockReset().mockResolvedValue({ success: true });
  mockReportFunnel.mockReset();
});

function renderDialog(onRegistered = vi.fn()) {
  render(<SignUpDialog onClose={vi.fn()} onRegistered={onRegistered} />);
  return onRegistered;
}

test('the verification code accepts letters, not just digits', () => {
  renderDialog();
  fireEvent.change(codeField(), { target: { value: 'A1b2C3' } });
  expect(codeField().value).toBe('A1b2C3');
});

test('case is preserved — we do not know the upstream compares case-insensitively', () => {
  renderDialog();
  fireEvent.change(codeField(), { target: { value: 'abcdef' } });
  expect(codeField().value).toBe('abcdef');
});

test('whitespace dragged in from an email paste is stripped', () => {
  renderDialog();
  fireEvent.change(codeField(), { target: { value: ' A1B2 C3 ' } });
  expect(codeField().value).toBe('A1B2C3');
});

test('the code is capped at 6 characters', () => {
  renderDialog();
  fireEvent.change(codeField(), { target: { value: 'ABCDEFGHI' } });
  expect(codeField().value).toBe('ABCDEF');
});

test('submit stays disabled until every field is valid', async () => {
  const onRegistered = renderDialog();
  const submit = screen.getByRole('button', { name: /pages.signup.submit/i });
  expect(submit).toBeDisabled();

  fireEvent.change(screen.getByLabelText(/pages.signup.email/i), {
    target: { value: 'a@b.com' },
  });
  fireEvent.change(screen.getByLabelText(/^pages.signup.password/i), {
    target: { value: 'Aa1!aaaa' },
  });
  fireEvent.change(screen.getByLabelText(/pages.signup.confirmPassword/i), {
    target: { value: 'Aa1!aaaa' },
  });
  // Still short a code.
  expect(submit).toBeDisabled();

  fireEvent.change(codeField(), { target: { value: 'A1B2C3' } });
  expect(submit).not.toBeDisabled();

  fireEvent.click(submit);
  await vi.waitFor(() => expect(mockSignup).toHaveBeenCalled());
  expect(mockSignup).toHaveBeenCalledWith('a@b.com', 'Aa1!aaaa', 'A1B2C3');
  await vi.waitFor(() => expect(onRegistered).toHaveBeenCalled());
});

test('mismatched passwords block submission', () => {
  renderDialog();
  fireEvent.change(screen.getByLabelText(/pages.signup.email/i), {
    target: { value: 'a@b.com' },
  });
  fireEvent.change(screen.getByLabelText(/^pages.signup.password/i), {
    target: { value: 'Aa1!aaaa' },
  });
  fireEvent.change(screen.getByLabelText(/pages.signup.confirmPassword/i), {
    target: { value: 'Aa1!bbbb' },
  });
  fireEvent.change(codeField(), { target: { value: 'A1B2C3' } });
  expect(screen.getByRole('button', { name: /pages.signup.submit/i })).toBeDisabled();
});

const emailField = () => screen.getByLabelText(/pages.signup.email/i);

test('pressing Escape closes the dialog', () => {
  const onClose = vi.fn();
  render(<SignUpDialog onClose={onClose} onRegistered={vi.fn()} />);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('a backdrop press-and-release closes the dialog', () => {
  const onClose = vi.fn();
  render(<SignUpDialog onClose={onClose} onRegistered={vi.fn()} />);
  // Requires BOTH mousedown and mouseup on the overlay itself.
  const overlay = screen.getByRole('dialog');
  fireEvent.mouseDown(overlay);
  fireEvent.mouseUp(overlay);
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('a drag from inside the card ending on the backdrop does NOT close', () => {
  const onClose = vi.fn();
  render(<SignUpDialog onClose={onClose} onRegistered={vi.fn()} />);
  fireEvent.mouseDown(screen.getByText('pages.signup.title')); // down inside
  fireEvent.mouseUp(screen.getByRole('dialog')); // up on backdrop
  expect(onClose).not.toHaveBeenCalled();
});

test('a drag from the backdrop ending inside the card does NOT close', () => {
  const onClose = vi.fn();
  render(<SignUpDialog onClose={onClose} onRegistered={vi.fn()} />);
  fireEvent.mouseDown(screen.getByRole('dialog')); // down on backdrop
  fireEvent.mouseUp(screen.getByText('pages.signup.title')); // up inside
  expect(onClose).not.toHaveBeenCalled();
});

test('Escape does NOT close while a request is in flight', async () => {
  const onClose = vi.fn();
  // A send-code that never resolves keeps `sending` true (busy).
  mockSendCode.mockReset().mockReturnValue(new Promise(() => {}));
  render(<SignUpDialog onClose={onClose} onRegistered={vi.fn()} />);
  fireEvent.change(emailField(), { target: { value: 'a@b.com' } });
  fireEvent.click(screen.getByRole('button', { name: /pages.signup.sendCode/i }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /pages.signup.sending/i })).toBeTruthy(),
  );
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(onClose).not.toHaveBeenCalled();
});

test('a send-code failure shows a generic message but reports the real reason', async () => {
  mockSendCode.mockReset().mockRejectedValue(new Error('email already registered'));
  renderDialog();
  fireEvent.change(emailField(), { target: { value: 'Probe@X.com' } });
  fireEvent.click(screen.getByRole('button', { name: /pages.signup.sendCode/i }));
  // UI must not echo the enumerating upstream message.
  await waitFor(() => expect(screen.getByText('pages.signup.sendFailed')).toBeTruthy());
  expect(screen.queryByText(/already registered/i)).toBeNull();
  // But the real reason (with a normalised email) reaches the funnel.
  expect(mockReportFunnel).toHaveBeenCalledWith(
    'signup_send_code_failed', 'probe@x.com', 'email already registered',
  );
});

test('changing the email after a code was sent resets the code-sent state', async () => {
  renderDialog();
  fireEvent.change(emailField(), { target: { value: 'a@b.com' } });
  fireEvent.click(screen.getByRole('button', { name: /pages.signup.sendCode/i }));
  // After a successful send the button flips to the resend cooldown.
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /pages.signup.resendIn/i })).toBeTruthy(),
  );
  // Editing the email must invalidate the code that was sent to the old address.
  fireEvent.change(emailField(), { target: { value: 'c@d.com' } });
  expect(screen.getByRole('button', { name: /pages.signup.sendCode/i })).toBeTruthy();
});
