/**
 * @file_name: ForgotPasswordCard.tsx
 * @description: Cloud-mode forgot-password flow. Cloud passwords are NetMind
 * passwords, so we drive NetMind's own reset directly (sendCode type=2 ->
 * resetPassword) — no backend involved. One modal, two steps: email -> code,
 * then code + new password -> done.
 */
import { useState } from 'react';
import { Button, FormField, TextInput } from '@/components/nm';
import { useNetmindAuth } from '@/lib/netmindAuth/useNetmindAuth';

export function ForgotPasswordCard({ onClose }: { onClose: () => void }) {
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [codeSent, setCodeSent] = useState(false);
  const [done, setDone] = useState(false);
  const netmind = useNetmindAuth();

  const sendCode = async () => {
    if (await netmind.sendResetCode(email.trim())) setCodeSent(true);
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
              Password updated
            </h2>
            <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
              You can now sign in with your new password.
            </p>
            <Button variant="primary" onClick={onClose} className="w-full">
              Back to sign in
            </Button>
          </>
        ) : (
          <>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--nm-ink)' }}>
              Reset password
            </h2>
            <p className="text-sm" style={{ color: 'var(--nm-ink70)' }}>
              We&apos;ll email a verification code to your NetMind account.
            </p>

            <FormField label="Email">
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
                <FormField label="Verification code">
                  <TextInput
                    value={code}
                    placeholder="Verification code"
                    onChange={(e) => setCode(e.target.value)}
                    disabled={netmind.loading}
                    className="h-11"
                  />
                </FormField>
                <FormField label="New password">
                  <TextInput
                    type="password"
                    value={newPassword}
                    placeholder="New password"
                    onChange={(e) => setNewPassword(e.target.value)}
                    disabled={netmind.loading}
                    className="h-11"
                  />
                </FormField>
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
                Cancel
              </Button>
              {!codeSent ? (
                <Button
                  variant="primary"
                  onClick={() => void sendCode()}
                  disabled={!email.trim() || netmind.loading}
                  loading={netmind.loading}
                  className="flex-1"
                >
                  Send code
                </Button>
              ) : (
                <Button
                  variant="primary"
                  onClick={() => void reset()}
                  disabled={!code.trim() || !newPassword || netmind.loading}
                  loading={netmind.loading}
                  className="flex-1"
                >
                  Reset password
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
