/**
 * @file_name: SignUpDialog.tsx
 * @description: Self-serve account creation, on our own page.
 *
 * "Create account" used to open netmind.ai in a new tab and hand the user off
 * mid-flow; they came back (if they came back) to a login form. This dialog
 * keeps the whole thing here: email → emailed code → password → signed in.
 *
 * Three steps, one screen. A wizard was tempting but wrong for three fields:
 * the code has to be requested before it can be typed, and that is the only
 * ordering constraint — everything else the user can fill in whatever order
 * they like, and seeing all of it at once is what makes the form feel short.
 *
 * SECRETS: the password and the code are component state and are passed only
 * to `api.*`. They are never logged, never put in the URL, never sent to
 * analytics — the spec calls this out explicitly and it is easy to violate by
 * adding a well-meaning debug line.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, X } from 'lucide-react';
import { Button, FormField, TextInput } from '@/components/nm';
import { api } from '@/lib/api';
import { PASSWORD_RULES } from '@/lib/netmindAuth/passwordPolicy';


/** Spec: "建议页面发送验证码后增加 60 秒倒计时，避免重复发送." The backend
 *  rate-limits on the same beat, so a user who beats the timer gets a 429
 *  rather than a second email. */
const RESEND_COOLDOWN_S = 60;

interface Props {
  onClose: () => void;
  /**
   * Called with the credentials just used, once the account exists.
   *
   * The dialog deliberately does NOT sign in itself: the login page already
   * owns that (token exchange, session store, first-agent routing), and a
   * second copy here would be a second place for those to drift.
   */
  onRegistered: (email: string, password: string) => void | Promise<void>;
}

export function SignUpDialog({ onClose, onRegistered }: Props) {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [code, setCode] = useState('');
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [error, setError] = useState('');

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  // A request in flight (sending a code / submitting) must not be dismissed out
  // from under itself: closing mid-submit unmounts the component, so a later
  // setError lands on nothing — the dialog vanishes with no error and no
  // account, having burned the emailed code. All dismissal paths check this.
  const busy = sending || submitting;

  // Standard modal dismissal: Escape closes; backdrop-click is handled on the
  // overlay's mousedown+mouseup below. (Without either, the only way out was X.)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, busy]);

  // Backdrop dismissal fires only when BOTH the mousedown and the mouseup landed
  // on the overlay itself — so selecting text inside an input and releasing
  // outside the card (a drag) does not count as a backdrop click and close it.
  const downOnBackdrop = useRef(false);

  // A code is sent to a specific address; if the user edits the email after
  // requesting it, the sent code and its cooldown no longer apply — reset them
  // so the UI can't imply a code is valid for the new address.
  const onEmailChange = (value: string) => {
    setEmail(value);
    if (codeSent || cooldown > 0 || code) {
      setCodeSent(false);
      setCooldown(0);
      setCode('');
    }
  };

  const failedRules = useMemo(
    () => PASSWORD_RULES.filter((r) => !r.test(password)),
    [password],
  );
  const emailLooksValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  const passwordsMatch = password.length > 0 && password === confirm;
  const canSubmit =
    emailLooksValid &&
    failedRules.length === 0 &&
    passwordsMatch &&
    code.trim().length === 6 &&
    !submitting;

  const sendCode = useCallback(async () => {
    setError('');
    setSending(true);
    try {
      const res = await api.sendSignupCode(email.trim().toLowerCase());
      if (res?.success === false) throw new Error(res.error || '');
      setCodeSent(true);
      setCooldown(RESEND_COOLDOWN_S);
    } catch (e) {
      // Do NOT echo the upstream message to the USER: NetMind's "this email is
      // already registered" would let the send-code form enumerate accounts.
      // But DON'T discard the reason — a transport failure never even reaches
      // the server, so the funnel is the only trace it gets (mirrors
      // useNetmindAuth's reportAuthFunnel). Only email + message go to
      // analytics — never the code (see this file's header).
      const message = e instanceof Error ? e.message : 'send code failed';
      setError(t('pages.signup.sendFailed'));
      api.reportAuthFunnel('signup_send_code_failed', email.trim().toLowerCase(), message);
      // NOTE: this masks the UI only. Fully closing registration enumeration
      // needs the backend /register/sendCode to return a uniform response —
      // tracked separately (out of Mark's login-scoped item [2]).
    } finally {
      setSending(false);
    }
  }, [email, t]);

  const submit = useCallback(async () => {
    setError('');
    setSubmitting(true);
    try {
      const res = await api.signup(email.trim().toLowerCase(), password, code.trim());
      if (res?.success === false) throw new Error(res.error || '');
      // Hand the credentials straight to the page's sign-in — asking someone to
      // re-type them one second later would be theatre.
      await onRegistered(email.trim().toLowerCase(), password);
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : t('pages.signup.failed'));
    } finally {
      setSubmitting(false);
    }
  }, [email, password, code, onRegistered, t]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto px-4 py-8"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="signup-title"
      // Backdrop dismissal: require BOTH the mousedown and the mouseup to land
      // on the overlay itself, so a text-selection drag that crosses the card
      // edge in EITHER direction is not a backdrop click. Never while busy.
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onMouseUp={(e) => {
        if (!busy && downOnBackdrop.current && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="w-full max-w-lg p-8 space-y-5"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          borderRadius: 'var(--radius-md)',
          boxShadow: 'var(--nm-elev-3)',
        }}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2
              id="signup-title"
              className="text-xl font-semibold"
              style={{ color: 'var(--nm-ink)' }}
            >
              {t('pages.signup.title')}
            </h2>
            <p className="mt-1 text-sm" style={{ color: 'var(--nm-ink70)' }}>
              {t('pages.signup.subtitle')}
            </p>
          </div>
          <button
            type="button"
            onClick={() => { if (!busy) onClose(); }}
            disabled={busy}
            aria-label={t('common.close')}
            className="p-1 rounded hover:opacity-70 disabled:opacity-40"
            style={{ color: 'var(--nm-ink50)' }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <FormField label={t('pages.signup.email')} required>
          <TextInput
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => onEmailChange(e.target.value)}
            placeholder="you@example.com"
          />
        </FormField>

        <FormField label={t('pages.signup.password')} required>
          <TextInput
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </FormField>

        {/* Live checklist rather than an error after submit: the rules are
            specific enough that guessing is genuinely annoying. */}
        <ul className="grid grid-cols-2 gap-x-4 gap-y-1 -mt-2">
          {PASSWORD_RULES.map((rule) => {
            const ok = rule.test(password);
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

        <FormField
          label={t('pages.signup.confirmPassword')}
          required
          error={
            confirm.length > 0 && !passwordsMatch
              ? t('pages.signup.mismatch')
              : undefined
          }
        >
          <TextInput
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </FormField>

        {/* The Send button sits OUTSIDE FormField on purpose: FormField injects
            its label's `htmlFor` id into its FIRST child, so wrapping the input
            and the button in a div made the label point at the div — a label
            that labels nothing. */}
        <div className="flex items-end gap-2">
          <FormField
            label={t('pages.signup.code')}
            required
            hint={codeSent ? t('pages.signup.codeSent') : undefined}
            className="flex-1"
          >
            <TextInput
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              // The code is ALPHANUMERIC, not digits. A digits-only filter here
              // silently ate every letter, and inputMode="numeric" meant a
              // phone keypad could not even type one. Strip whitespace only —
              // pasting from an email often drags a space along — and leave
              // case alone, because we do not know that the upstream compares
              // case-insensitively.
              onChange={(e) => setCode(e.target.value.replace(/\s/g, '').slice(0, 6))}
              placeholder={t('pages.signup.codePlaceholder')}
            />
          </FormField>
          <Button
            variant="secondary"
            onClick={sendCode}
            disabled={!emailLooksValid || sending || cooldown > 0}
            className="whitespace-nowrap"
          >
            {cooldown > 0
              ? t('pages.signup.resendIn', { seconds: cooldown })
              : sending
                ? t('pages.signup.sending')
                : t('pages.signup.sendCode')}
          </Button>
        </div>

        {error && (
          <p className="text-sm" style={{ color: 'var(--color-error)' }}>
            {error}
          </p>
        )}

        <Button
          variant="primary"
          onClick={submit}
          disabled={!canSubmit}
          className="w-full"
        >
          {submitting ? t('pages.signup.creating') : t('pages.signup.submit')}
        </Button>

        <p className="text-center text-xs" style={{ color: 'var(--nm-ink50)' }}>
          <button
            type="button"
            onClick={() => { if (!busy) onClose(); }}
            disabled={busy}
            className="underline hover:opacity-70 disabled:opacity-40"
          >
            {t('pages.signup.haveAccount')}
          </button>
        </p>
      </div>
    </div>
  );
}
