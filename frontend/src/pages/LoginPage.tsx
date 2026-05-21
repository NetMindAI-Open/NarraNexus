/**
 * Login Page · NM Design System (M3 Wave 2)
 *
 * Supports both Local (user_id only) and Cloud (user_id + password) modes.
 * Restyled to use NM BracketMarkLogo + FormField/TextInput + Button + Chip
 * primitives. Layout preserves the original document-style centered card.
 */

import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { ArrowRight, ArrowLeft, UserPlus, Cloud } from 'lucide-react';
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

export function LoginPage() {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  const navigate = useNavigate();
  const location = useLocation();
  const { isDark } = useTheme();
  const { login, setAgents, setAgentId } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const setMode = useRuntimeStore((s) => s.setMode);
  const setCloudApiUrl = useRuntimeStore((s) => s.setCloudApiUrl);

  const isCloudMode = mode === 'cloud-app' || mode === 'cloud-web';
  const canChangeMode = mode !== 'cloud-web';

  const handleChangeMode = () => {
    setCloudApiUrl('');
    setMode(null);
    navigate('/mode-select');
  };

  const handleLogin = async () => {
    if (!userId.trim()) {
      setError('Please enter your User ID');
      return;
    }
    if (isCloudMode && !password) {
      setError('Please enter your password');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const loginRes = await api.login(userId.trim(), isCloudMode ? password : undefined);
      if (!loginRes.success) {
        setError(loginRes.error || 'Login failed');
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
      setError('Connection failed. Please try again.');
      console.error('Login error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleLogin();
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
        {canChangeMode && (
          <button
            type="button"
            onClick={handleChangeMode}
            className="flex items-center gap-1.5 text-[11px] mb-6 -mt-2 transition-colors hover:opacity-100 opacity-60"
            style={{
              color: 'var(--nm-ink50)',
              fontFamily: 'var(--font-mono)',
              textTransform: 'uppercase',
              letterSpacing: '0.10em',
            }}
          >
            <ArrowLeft className="w-3 h-3" />
            <span>Change mode</span>
          </button>
        )}

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
            {isCloudMode ? 'Cloud · Access' : 'Local · Access'}
          </div>
          {isCloudMode && (
            <Chip species="silicon" leading={<Cloud className="w-3 h-3" />}>
              Cloud mode
            </Chip>
          )}
        </div>

        <Divider />

        {/* Form */}
        <div className="space-y-5 mt-6">
          <FormField label="User ID">
            <TextInput
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="your_username"
              disabled={loading}
              error={!!error}
              autoFocus
              className="h-12"
            />
          </FormField>

          {isCloudMode && (
            <FormField label="Password">
              <TextInput
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="••••••••"
                disabled={loading}
                error={!!error}
                className="h-12"
              />
            </FormField>
          )}

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
            onClick={handleLogin}
            disabled={loading || !userId.trim() || (isCloudMode && !password)}
            loading={loading}
            className="w-full"
            trailing={!loading ? <ArrowRight className="w-4 h-4" /> : undefined}
          >
            {loading ? 'Connecting…' : isCloudMode ? 'Sign In' : 'Access Terminal'}
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
                or
              </span>
            </div>
          </div>

          {isCloudMode ? (
            <Button
              variant="secondary"
              onClick={() => navigate('/register')}
              className="w-full"
              leading={<UserPlus className="w-4 h-4" />}
            >
              Create Account
            </Button>
          ) : (
            <Button
              variant="secondary"
              onClick={() => setShowCreateDialog(true)}
              className="w-full"
              leading={<UserPlus className="w-4 h-4" />}
            >
              Create New User
            </Button>
          )}
        </div>

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
    </div>
  );
}

export default LoginPage;
