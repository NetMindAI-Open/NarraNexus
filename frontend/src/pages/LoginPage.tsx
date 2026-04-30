/**
 * Login Page - Supports both Local (user_id only) and Cloud (user_id + password) modes
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, ArrowRight, ArrowLeft, Sparkles, UserPlus, Cloud } from 'lucide-react';
import { Button, Input } from '@/components/ui';
import { useTheme } from '@/hooks';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { CreateUserDialog } from './CreateUserDialog';

export function LoginPage() {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  const navigate = useNavigate();
  const { isDark } = useTheme();
  const { login, setAgents, setAgentId } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const setMode = useRuntimeStore((s) => s.setMode);
  const setCloudApiUrl = useRuntimeStore((s) => s.setCloudApiUrl);

  const isCloudMode = mode === 'cloud-app' || mode === 'cloud-web';

  // "Change Mode" — clears the selected mode so the router sends the user
  // back to /mode-select. We also clear cloudApiUrl so the next cloud pick
  // prompts for a fresh URL instead of silently reusing the last one.
  // `cloud-web` (force-cloud deployment) has no real "other mode" to
  // switch to, so the button is hidden in that case.
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

      // Store token FIRST so subsequent API calls can use it
      login(userId.trim(), loginRes.token || undefined, loginRes.role || undefined);

      const agentsRes = await api.getAgents(userId.trim());
      if (agentsRes.success && agentsRes.agents.length > 0) {
        setAgents(agentsRes.agents);
        setAgentId(agentsRes.agents[0].agent_id);
      }
      navigate('/');
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
    <div className="login-container">
      <div className="login-card animate-scale-in">
        {/* Change Mode — lets the user back out of an accidental pick */}
        {canChangeMode && (
          <button
            type="button"
            onClick={handleChangeMode}
            className="flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)] hover:text-[var(--accent-primary)] transition-colors mb-4 -mt-2"
          >
            <ArrowLeft className="w-3 h-3" />
            <span>Change mode</span>
          </button>
        )}

        {/* Document header — archive style */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-6">
            <img src={isDark ? '/logo-dark-mode.png' : '/logo-light-mode.png'} alt="NarraNexus" className="h-8 w-auto object-contain" />
            <span className="text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.22em] text-[var(--text-tertiary)]">
              NetMind · Access
            </span>
          </div>
          <h1
            className="font-[family-name:var(--font-display)] font-bold text-[var(--text-primary)] mb-3"
            style={{ fontSize: 'clamp(2rem, 6vw, 2.75rem)', lineHeight: 1.02, letterSpacing: '-0.025em' }}
          >
            NarraNexus
          </h1>
          <hr className="archive-rule-thick" style={{ margin: '0 0 1rem 0' }} />
          <p className="text-[var(--text-secondary)] text-sm font-light">
            {isCloudMode ? 'Cloud platform · ' : 'Intelligent agent platform · '}
            <span className="font-[family-name:var(--font-mono)] text-[11px] uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
              {isCloudMode ? '01 · Sign in' : '01 · Credentials'}
            </span>
          </p>
          {isCloudMode && (
            <div className="mt-3 inline-flex items-center gap-1.5 text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] text-[var(--text-tertiary)] border border-[var(--border-subtle)] px-2 py-1">
              <Cloud className="w-3 h-3" />
              Cloud mode
            </div>
          )}
        </div>

        {/* Login Form */}
        <div className="space-y-5">
          <div className="space-y-2">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              User ID
            </label>
            <Input
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="your_username"
              disabled={loading}
              error={!!error}
              className={cn('h-12 text-base font-mono', 'bg-[var(--bg-sunken)]')}
              autoFocus
            />
          </div>

          {isCloudMode && (
            <div className="space-y-2">
              <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
                Password
              </label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="••••••••"
                disabled={loading}
                error={!!error}
                className={cn('h-12 text-base', 'bg-[var(--bg-sunken)]')}
              />
            </div>
          )}

          {error && (
            <p className="text-xs text-[var(--color-error)] animate-slide-up flex items-center gap-1.5">
              <span className="w-1 h-1 rounded-full bg-[var(--color-error)]" />
              {error}
            </p>
          )}

          <Button
            variant="accent"
            onClick={handleLogin}
            disabled={loading || !userId.trim() || (isCloudMode && !password)}
            className="w-full h-12 text-base font-semibold group"
          >
            {loading ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                <span>Connecting...</span>
              </>
            ) : (
              <>
                <span>{isCloudMode ? 'Sign In' : 'Access Terminal'}</span>
                <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
              </>
            )}
          </Button>

          {/* Divider */}
          <div className="relative py-4">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-[var(--border-subtle)]" />
            </div>
            <div className="relative flex justify-center">
              <span className="px-3 bg-[var(--glass-bg)] text-[10px] text-[var(--text-tertiary)] uppercase tracking-wider">
                or
              </span>
            </div>
          </div>

          {/* Create User (local) or Register (cloud) */}
          {isCloudMode ? (
            <Button
              variant="outline"
              onClick={() => navigate('/register')}
              className="w-full h-11 text-sm"
            >
              <UserPlus className="w-4 h-4" />
              <span>Create Account</span>
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={() => setShowCreateDialog(true)}
              className="w-full h-11 text-sm"
            >
              <UserPlus className="w-4 h-4" />
              <span>Create New User</span>
            </Button>
          )}
        </div>

        {/* Footer */}
        <div className="mt-10 pt-5 border-t border-[var(--rule)]">
          <div className="flex items-center justify-between text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.18em] text-[var(--text-tertiary)]">
            <span className="flex items-center gap-1.5">
              <Sparkles className="w-3 h-3" />
              NetMind.AI
            </span>
            <span>v1.0.0</span>
          </div>
        </div>
      </div>

      {/* Create User Dialog (local mode only) */}
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
