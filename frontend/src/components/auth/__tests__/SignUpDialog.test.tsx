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
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const mockSendCode = vi.fn();
const mockSignup = vi.fn();
vi.mock('@/lib/api', () => ({
  api: {
    sendSignupCode: (...a: unknown[]) => mockSendCode(...a),
    signup: (...a: unknown[]) => mockSignup(...a),
  },
}));

import { SignUpDialog } from '../SignUpDialog';

const codeField = () =>
  screen.getByLabelText(/pages.signup.code/i) as HTMLInputElement;

beforeEach(() => {
  mockSendCode.mockReset().mockResolvedValue({ success: true });
  mockSignup.mockReset().mockResolvedValue({ success: true });
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
