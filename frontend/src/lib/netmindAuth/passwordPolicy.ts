/**
 * @file_name: passwordPolicy.ts
 * @author: NarraNexus
 * @date: 2026-08-12
 * @description: NetMind's password policy, mirrored client-side so BOTH the
 * sign-up form and the forgot-password form validate before hitting NetMind.
 *
 * One shared copy stops the two forms drifting apart — and, crucially for the
 * reset flow, it means `resetPassword` never receives a password-policy
 * rejection. That is what lets resetPassword mask every upstream rejection to
 * one generic message (anti-enumeration) WITHOUT hiding a fixable weak-password
 * behind a "code invalid" error (the dead-loop the reset form otherwise had).
 *
 * Matches backend `password_policy_error` (netmind_register_client.py).
 */

export interface PasswordRule {
  id: string;
  test: (value: string) => boolean;
}

export const PASSWORD_RULES: PasswordRule[] = [
  { id: 'length', test: (v) => v.length >= 8 && v.length <= 16 },
  { id: 'upper', test: (v) => /[A-Z]/.test(v) },
  { id: 'lower', test: (v) => /[a-z]/.test(v) },
  { id: 'digit', test: (v) => /\d/.test(v) },
  { id: 'special', test: (v) => /[^A-Za-z0-9]/.test(v) },
];

/** The rules a password does NOT yet satisfy (empty = valid). */
export function failedPasswordRules(password: string): PasswordRule[] {
  return PASSWORD_RULES.filter((rule) => !rule.test(password));
}
