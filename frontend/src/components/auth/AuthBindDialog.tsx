/**
 * @file_name: AuthBindDialog.tsx
 * @description: First-time third-party (OAuth) account binding dialog.
 * NetMind's userCallBack returns a bandType when a Google/MS/GitHub
 * identity isn't yet linked to a NetMind account:
 *   1 = collect email + email verification code
 *   2 = confirm the third-party email
 *   3 = bind to an existing NetMind account by that email
 * Only bandType 1 needs inputs; 2/3 are confirm-and-continue.
 */
import { useState } from 'react';
import { Button, FormField, TextInput } from '@/components/nm';
import type { AuthBindInfo } from '@/lib/netmindAuth/types';

interface Props {
  bindInfo: AuthBindInfo;
  loading: boolean;
  error: string;
  onSubmit: (extra: { email?: string; verifyCode?: string }) => void;
  onClose: () => void;
}

export function AuthBindDialog({ bindInfo, loading, error, onSubmit, onClose }: Props) {
  const [email, setEmail] = useState(bindInfo.thirdEmail || bindInfo.canBandEmail || '');
  const [verifyCode, setVerifyCode] = useState('');
  const needsInputs = bindInfo.bandType === 1;

  const handleSubmit = () => {
    onSubmit(needsInputs ? { email, verifyCode } : {});
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: 'rgba(0,0,0,0.4)' }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-sm p-8"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          borderRadius: 'var(--radius-md)',
        }}
      >
        <h2 className="text-lg mb-4" style={{ color: 'var(--nm-ink)' }}>
          Link your account
        </h2>
        {bindInfo.bandType === 2 && (
          <p className="text-sm mb-4" style={{ color: 'var(--nm-ink70)' }}>
            Confirm linking the email <strong>{bindInfo.thirdEmail}</strong> to your NetMind
            account.
          </p>
        )}
        {bindInfo.bandType === 3 && (
          <p className="text-sm mb-4" style={{ color: 'var(--nm-ink70)' }}>
            An account already exists for <strong>{bindInfo.canBandEmail}</strong>. Bind this
            sign-in to it.
          </p>
        )}
        {needsInputs && (
          <div className="space-y-4 mb-4">
            <FormField label="Email">
              <TextInput
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </FormField>
            <FormField label="Verification code">
              <TextInput
                type="text"
                value={verifyCode}
                onChange={(e) => setVerifyCode(e.target.value)}
                placeholder="6-digit code"
              />
            </FormField>
          </div>
        )}
        {error && (
          <p className="text-xs mb-3" style={{ color: 'var(--color-error)' }} role="alert">
            {error}
          </p>
        )}
        <div className="flex gap-3">
          <Button variant="secondary" onClick={onClose} className="flex-1" disabled={loading}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            className="flex-1"
            loading={loading}
            disabled={loading || (needsInputs && (!email || !verifyCode))}
          >
            Confirm
          </Button>
        </div>
      </div>
    </div>
  );
}
