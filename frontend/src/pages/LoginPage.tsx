/**
 * Login Page · NM Design System (M3 Wave 2)
 *
 * Cloud mode: NetMind email/password + OAuth (Google, Microsoft, GitHub) + bind dialog.
 * Local mode: user_id only (unchanged).
 * Layout preserves the original document-style centered card.
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useLocation } from 'react-router-dom';
import { ArrowRight, UserPlus, Cloud } from 'lucide-react';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { useTheme } from '@/hooks';
import { api } from '@/lib/api';
import {
  Button,
  FormField,
  TextInput,
  Chip,
  Divider,
} from '@/components/nm';
import { isSafeReturnTo } from '@/lib/safe-return';
import { CreateUserDialog } from './CreateUserDialog';
import { useNetmindAuth } from '@/lib/netmindAuth/useNetmindAuth';
import { AuthBindDialog } from '@/components/auth/AuthBindDialog';
import { ForgotPasswordCard } from '@/components/auth/ForgotPasswordCard';
import { getNetmindConfig } from '@/lib/runtimeConfig';

export function LoginPage() {
  const [userId, setUserId] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showForgot, setShowForgot] = useState(false);

  const navigate = useNavigate();
  const { t } = useTranslation();
  const location = useLocation();
  const { isDark } = useTheme();
  const { login, setNetmindToken, setAgents, setAgentId } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);

  const isCloudMode = mode === 'cloud-web';

  const netmind = useNetmindAuth({
    onSuccess: async (res, loginToken) => {
      if (!res.success || !res.user_id) {
        setError(res.error || t('pages.login.loginFailed'));
        return;
      }
      login(res.user_id, res.token || undefined, res.role || undefined, {
        displayName: res.display_name,
        email: res.email,
      });
      setNetmindToken(loginToken);
      const agentsRes = await api.getAgents();
      if (agentsRes.success && agentsRes.agents.length > 0) {
        setAgents(agentsRes.agents);
        setAgentId(agentsRes.agents[0].agent_id);
      }
      const params = new URLSearchParams(location.search);
      const next = params.get('next');
      navigate(isSafeReturnTo(next) ? next : '/');
    },
  });

  // Local-mode only login (cloud mode uses netmind hook instead)
  const handleLocalLogin = async () => {
    if (!userId.trim()) {
      setError(t('pages.login.enterUserId'));
      return;
    }

    setLoading(true);
    setError('');

    try {
      const loginRes = await api.login(userId.trim(), undefined);
      if (!loginRes.success) {
        setError(loginRes.error || t('pages.login.loginFailed'));
        setLoading(false);
        return;
      }
      login(userId.trim(), loginRes.token || undefined, loginRes.role || undefined);

      const agentsRes = await api.getAgents();
      if (agentsRes.success && agentsRes.agents.length > 0) {
        setAgents(agentsRes.agents);
        setAgentId(agentsRes.agents[0].agent_id);
      }
      // Return-URL flow: ProtectedRoute redirects unauthenticated visitors
      // to /login?next=<encoded-path>. After auth, send them back to that
      // URL — but only if it's a same-origin relative path (open-redirect
      // guard, see lib/safe-return). For unknown / unsafe / absent next,
      // fall through to the default RootRedirect at "/".
      const params = new URLSearchParams(location.search);
      const next = params.get('next');
      navigate(isSafeReturnTo(next) ? next : '/');
    } catch (err) {
      setError(t('pages.login.connectionFailed'));
      console.error('Login error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleLocalKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') void handleLocalLogin();
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center px-4"
      style={{ background: 'var(--nm-paper)' }}
    >
      <div
        className="w-full max-w-md p-10 animate-scale-in"
        style={{
          background: 'var(--nm-card)',
          border: '1px solid var(--nm-hairline)',
          borderRadius: 'var(--radius-md)',
        }}
      >
        {/* Brand header — original NarraNexus logo preserved */}
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <img
            src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
            alt="NarraNexus"
            className="h-16 w-auto object-contain"
          />
          <div
            className="text-[10px] uppercase tracking-[0.22em]"
            style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
          >
            {isCloudMode ? t('pages.login.cloudAccess') : t('pages.login.localAccess')}
          </div>
          {isCloudMode && (
            <Chip species="silicon" leading={<Cloud className="w-3 h-3" />}>
              {t('pages.login.cloudMode')}
            </Chip>
          )}
          {isCloudMode && (
            <p className="text-xs" style={{ color: 'var(--nm-ink50)' }}>
              {t('pages.login.signInWithNetmind')}
            </p>
          )}
        </div>

        <Divider />

        {/* Form */}
        {isCloudMode ? (
          /* Cloud: NetMind email + password + OAuth */
          <div className="space-y-5 mt-6">
            <div
              className="rounded-xl p-3 text-xs leading-relaxed"
              style={{
                background: 'var(--nm-card)',
                border: '1px solid var(--nm-hairline)',
                color: 'var(--nm-ink70)',
              }}
              role="status"
            >
              <strong>{t('pages.login.migrationNoticeHeading')}</strong>{' '}
              {t('pages.login.migrationNoticeBody')}{' '}
              <a href="mailto:bin.liang@netmind.ai" className="underline">
                bin.liang@netmind.ai
              </a>
              .
            </div>

            <FormField label={t('pages.login.emailLabel')}>
              <TextInput
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                disabled={netmind.loading}
                error={!!(netmind.error || error)}
                autoFocus
                className="h-12"
              />
            </FormField>

            <FormField label={t('pages.login.passwordLabel')}>
              <TextInput
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                disabled={netmind.loading}
                error={!!(netmind.error || error)}
                className="h-12"
              />
            </FormField>

            <div className="flex justify-end -mt-1">
              <button
                type="button"
                onClick={() => setShowForgot(true)}
                className="text-xs opacity-60 hover:opacity-100 transition-opacity"
              >
                {t('pages.login.forgotPassword')}
              </button>
            </div>

            {(netmind.error || error) && (
              <p
                className="text-xs animate-slide-up flex items-center gap-1.5"
                style={{ color: 'var(--color-error)' }}
                role="alert"
              >
                <span
                  className="w-1 h-1 rounded-full inline-block"
                  style={{ background: 'var(--color-error)' }}
                />
                {netmind.error || error}
              </p>
            )}

            <Button
              variant="primary"
              size="lg"
              onClick={() => void netmind.emailLogin(email, password)}
              disabled={netmind.loading || !email.trim() || !password}
              loading={netmind.loading}
              className="w-full"
              trailing={!netmind.loading ? <ArrowRight className="w-4 h-4" /> : undefined}
            >
              {netmind.loading ? t('pages.login.connecting') : t('pages.login.signIn')}
            </Button>

            <div className="relative py-4">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t" style={{ borderColor: 'var(--nm-hairline)' }} />
              </div>
              <div className="relative flex justify-center">
                <span
                  className="px-3 text-[10px] uppercase tracking-wider"
                  style={{
                    background: 'var(--nm-card)',
                    color: 'var(--nm-ink50)',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  {t('pages.login.or')}
                </span>
              </div>
            </div>

            <Button
              variant="secondary"
              onClick={() => netmind.startOAuth('GOOGLE')}
              disabled={netmind.loading}
              className="w-full"
            >
              {t('pages.login.signInWithGoogle')}
            </Button>

            <Button
              variant="secondary"
              onClick={() => netmind.startOAuth('MICROSOFT')}
              disabled={netmind.loading}
              className="w-full"
            >
              {t('pages.login.signInWithMicrosoft')}
            </Button>

            <Button
              variant="secondary"
              onClick={() => netmind.startOAuth('GITHUB')}
              disabled={netmind.loading}
              className="w-full"
            >
              {t('pages.login.signInWithGithub')}
            </Button>

            <div className="relative py-4">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t" style={{ borderColor: 'var(--nm-hairline)' }} />
              </div>
              <div className="relative flex justify-center">
                <span
                  className="px-3 text-[10px] uppercase tracking-wider"
                  style={{
                    background: 'var(--nm-card)',
                    color: 'var(--nm-ink50)',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  {t('pages.login.or')}
                </span>
              </div>
            </div>

            <a
              href={getNetmindConfig().registerUrl || 'https://www.netmind.ai/sign/register'}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center justify-center gap-2 rounded-[var(--radius-sm)] font-medium transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--nm-ink)] h-10 px-4 text-sm bg-[color:var(--nm-raised)] text-[color:var(--nm-ink)] border border-[color:var(--nm-ink)] hover:bg-[color:var(--nm-paper-warm)] w-full"
            >
              <UserPlus className="w-4 h-4" />
              <span>{t('pages.login.createAccount')}</span>
            </a>
          </div>
        ) : (
          /* Local: user_id only — original flow preserved */
          <div className="space-y-5 mt-6">
            <FormField label={t('pages.login.userIdLabel')}>
              <TextInput
                type="text"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                onKeyDown={handleLocalKeyDown}
                placeholder="your_username"
                disabled={loading}
                error={!!error}
                autoFocus
                className="h-12"
              />
            </FormField>

            {error && (
              <p
                className="text-xs animate-slide-up flex items-center gap-1.5"
                style={{ color: 'var(--color-error)' }}
                role="alert"
              >
                <span
                  className="w-1 h-1 rounded-full inline-block"
                  style={{ background: 'var(--color-error)' }}
                />
                {error}
              </p>
            )}

            <Button
              variant="primary"
              size="lg"
              onClick={() => void handleLocalLogin()}
              disabled={loading || !userId.trim()}
              loading={loading}
              className="w-full"
              trailing={!loading ? <ArrowRight className="w-4 h-4" /> : undefined}
            >
              {loading ? t('pages.login.connecting') : t('pages.login.accessTerminal')}
            </Button>

            <div className="relative py-4">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t" style={{ borderColor: 'var(--nm-hairline)' }} />
              </div>
              <div className="relative flex justify-center">
                <span
                  className="px-3 text-[10px] uppercase tracking-wider"
                  style={{
                    background: 'var(--nm-card)',
                    color: 'var(--nm-ink50)',
                    fontFamily: 'var(--font-mono)',
                  }}
                >
                  {t('pages.login.or')}
                </span>
              </div>
            </div>

            <Button
              variant="secondary"
              onClick={() => setShowCreateDialog(true)}
              className="w-full"
              leading={<UserPlus className="w-4 h-4" />}
            >
              {t('pages.login.createNewUser')}
            </Button>
          </div>
        )}

        {/* Footer */}
        <div className="mt-10 pt-5 border-t" style={{ borderColor: 'var(--nm-hairline)' }}>
          <div
            className="flex items-center justify-between text-[10px] uppercase tracking-[0.18em]"
            style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
          >
            <span className="inline-flex items-center gap-2">
              <span
                className="w-1.5 h-1.5 rounded-full inline-block"
                style={{ background: 'var(--color-carbon)' }}
                aria-hidden
              />
              NetMind.AI
            </span>
            <span>v{__APP_VERSION__}</span>
          </div>
        </div>
      </div>

      {showCreateDialog && (
        <CreateUserDialog
          onClose={() => setShowCreateDialog(false)}
          onCreated={(id) => setUserId(id)}
        />
      )}

      {netmind.bindInfo && (
        <AuthBindDialog
          bindInfo={netmind.bindInfo}
          loading={netmind.loading}
          error={netmind.error}
          onSubmit={netmind.submitBind}
          onClose={netmind.closeBind}
        />
      )}

      {showForgot && (
        <ForgotPasswordCard onClose={() => setShowForgot(false)} />
      )}
    </div>
  );
}

export default LoginPage;
