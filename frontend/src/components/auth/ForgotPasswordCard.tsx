/**
 * @file_name: ForgotPasswordCard.tsx
 * @description: Cloud-mode forgot-password flow. Cloud passwords are NetMind
 * passwords, so we drive NetMind's own reset directly (sendCode type=2 ->
 * resetPassword) — no backend involved. One modal, two steps: email -> code,
 * then code + new password -> done.
 *
 * The new password is validated client-side against the shared PASSWORD_RULES
 * BEFORE submit. That is load-bearing for the anti-enumeration masking in
 * useNetmindAuth.resetPassword: because a policy-violating password can never
 * reach NetMind, the generic "code invalid" message resetPassword falls back to
 * can only ever mean a bad code / unknown email — it can't silently hide a
 * fixable weak password (which used to trap the user in a reset dead-loop).
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check } from 'lucide-react';
import { Button, FormField, TextInput } from '@/components/nm';
import { useNetmindAuth } from '@/lib/netmindAuth/useNetmindAuth';
import { PASSWORD_RULES, failedPasswordRules } from '@/lib/netmindAuth/passwordPolicy';

export function ForgotPasswordCard({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [codeSent, setCodeSent] = useState(false);
  const [done, setDone] = useState(false);
  const netmind = useNetmindAuth();

  const failedRules = useMemo(() => failedPasswordRules(newPassword), [newPassword]);
  const canReset = code.trim().length > 0 && failedRules.length === 0 && !netmind.loading;

  const sendCode = async () => {
    if (await netmind.sendResetCode(email.trim())) setCodeSent(true);
  };
  // Go back to the email step to fix a typo'd address (the flow always advances
  // for anti-enumeration, so a wrong email otherwise strands the user here).
  const changeEmail = () => {
    setCodeSent(false);
    setCode('');
    setNewPassword('');
  };
  const reset = async () => {
    if (await netmind.resetPassword(email.trim(), code.trim(), newPassword)) {
      setDone(true);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: 'rgba(0,0,0,0.4)' }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-sm p-6 space-y-4"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          borderRadius: 'var(--radius-md)',
          boxShadow: 'var(--nm-elev-3)',
        }}
      >
        {done ? (
          <>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--nm-ink)' }}>
              {t('pages.login.resetPasswordDone')}
            </h2>
            <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
              {t('pages.login.resetPasswordDoneBody')}
            </p>
            <Button variant="primary" onClick={onClose} className="w-full">
              {t('pages.login.backToSignIn')}
            </Button>
          </>
        ) : (
          <>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--nm-ink)' }}>
              {t('pages.login.resetPasswordTitle')}
            </h2>
            <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
              {codeSent
                ? t('pages.login.forgotCodeSentHint')
                : t('pages.login.forgotResetIntro')}
            </p>

            <FormField label={t('pages.login.emailLabel')}>
              <TextInput
                type="email"
                value={email}
                placeholder="you@example.com"
                onChange={(e) => setEmail(e.target.value)}
                disabled={codeSent || netmind.loading}
                className="h-11"
              />
            </FormField>

            {codeSent && (
              <>
                <div className="flex justify-end -mt-2">
                  <button
                    type="button"
                    onClick={changeEmail}
                    disabled={netmind.loading}
                    className="text-xs underline opacity-70 hover:opacity-100 disabled:opacity-40"
                  >
                    {t('pages.login.useDifferentEmail')}
                  </button>
                </div>
                <FormField label={t('pages.login.verificationCodeLabel')}>
                  <TextInput
                    value={code}
                    placeholder={t('pages.login.verificationCodeLabel')}
                    onChange={(e) => setCode(e.target.value)}
                    disabled={netmind.loading}
                    className="h-11"
                  />
                </FormField>
                <FormField label={t('pages.login.newPasswordLabel')}>
                  <TextInput
                    type="password"
                    value={newPassword}
                    placeholder={t('pages.login.newPasswordLabel')}
                    onChange={(e) => setNewPassword(e.target.value)}
                    disabled={netmind.loading}
                    className="h-11"
                  />
                </FormField>
                {/* Live policy checklist — a policy-failing password must not be
                    submittable (see file header + resetPassword masking). */}
                <ul className="grid grid-cols-2 gap-x-4 gap-y-1">
                  {PASSWORD_RULES.map((rule) => {
                    const ok = rule.test(newPassword);
                    return (
                      <li
                        key={rule.id}
                        className="flex items-center gap-1.5 text-[11px]"
                        style={{ color: ok ? 'var(--color-success)' : 'var(--nm-ink50)' }}
                      >
                        {ok ? <Check className="w-3 h-3" /> : <span className="w-3" />}
                        {t(`pages.signup.rule.${rule.id}`)}
                      </li>
                    );
                  })}
                </ul>
              </>
            )}

            {netmind.error && (
              <p
                className="text-xs"
                style={{ color: 'var(--color-error)' }}
                role="alert"
              >
                {netmind.error}
              </p>
            )}

            <div className="flex gap-3 pt-1">
              <Button
                variant="secondary"
                onClick={onClose}
                disabled={netmind.loading}
                className="flex-1"
              >
                {t('common.cancel')}
              </Button>
              {!codeSent ? (
                <Button
                  variant="primary"
                  onClick={() => void sendCode()}
                  disabled={!email.trim() || netmind.loading}
                  loading={netmind.loading}
                  className="flex-1"
                >
                  {t('pages.login.sendCode')}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  onClick={() => void reset()}
                  disabled={!canReset}
                  loading={netmind.loading}
                  className="flex-1"
                >
                  {t('pages.login.resetPasswordTitle')}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
