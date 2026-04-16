/**
 * Register Page - Cloud mode only, requires invite code
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Sparkles, UserPlus, Cloud, ArrowLeft } from 'lucide-react';
import { Button, Input } from '@/components/ui';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';

export function RegisterPage() {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const navigate = useNavigate();
  const { login, setAgents, setAgentId } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const setMode = useRuntimeStore((s) => s.setMode);
  const setCloudApiUrl = useRuntimeStore((s) => s.setCloudApiUrl);

  // See LoginPage.tsx for rationale on the Change Mode affordance.
  const canChangeMode = mode !== 'cloud-web';
  const handleChangeMode = () => {
    setCloudApiUrl('');
    setMode(null);
    navigate('/mode-select');
  };

  const handleRegister = async () => {
    setError('');

    if (!userId.trim()) {
      setError('Please enter a username');
      return;
    }
    if (userId.trim().length < 2 || userId.trim().length > 32) {
      setError('Username must be 2-32 characters');
      return;
    }
    if (!password || password.length < 6) {
      setError('Password must be at least 6 characters');
      return;
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    if (!inviteCode.trim()) {
      setError('Please enter the invite code');
      return;
    }

    setLoading(true);

    try {
      const res = await api.register(userId.trim(), password, inviteCode.trim());
      if (!res.success) {
        setError(res.error || 'Registration failed');
        setLoading(false);
        return;
      }

      // Auto-login after registration
      login(userId.trim(), res.token || undefined, 'user');

      // Fetch agents (will be empty for new user)
      try {
        const agentsRes = await api.getAgents(userId.trim());
        if (agentsRes.success && agentsRes.agents.length > 0) {
          setAgents(agentsRes.agents);
          setAgentId(agentsRes.agents[0].agent_id);
        }
      } catch {}

      navigate('/');
    } catch (err) {
      setError('Connection failed. Please try again.');
      console.error('Register error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleRegister();
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

        {/* Header */}
        <div className="text-center mb-10">
          <div className="relative inline-block mb-5">
            <div className="relative w-20 h-20 rounded-2xl bg-[var(--gradient-primary)] flex items-center justify-center overflow-hidden shadow-[var(--shadow-glow)]">
              <img
                src="/logo.png"
                alt="NetMind.AI"
                className="w-14 h-14 object-contain"
              />
              <div className="absolute -bottom-1 -right-1 w-7 h-7 rounded-full bg-[var(--bg-primary)] border-2 border-[var(--accent-primary)] flex items-center justify-center">
                <Cloud className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
              </div>
            </div>
          </div>

          <h1 className="text-3xl font-bold font-[family-name:var(--font-display)] text-[var(--text-primary)] mb-2 tracking-tight">
            Narra<span className="text-[var(--accent-primary)]">Nexus</span>
          </h1>
          <p className="text-[var(--text-secondary)] text-sm">Create your account</p>
          <p className="text-[10px] text-[var(--text-tertiary)] font-mono tracking-[0.2em] uppercase mt-1">
            Invite code required
          </p>
        </div>

        {/* Register Form */}
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              Username
            </label>
            <Input
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="choose_a_username"
              disabled={loading}
              className={cn('h-12 text-base font-mono', 'bg-[var(--bg-sunken)]')}
              autoFocus
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              Password
            </label>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="at least 6 characters"
              disabled={loading}
              className={cn('h-12 text-base', 'bg-[var(--bg-sunken)]')}
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              Confirm Password
            </label>
            <Input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="re-enter password"
              disabled={loading}
              className={cn('h-12 text-base', 'bg-[var(--bg-sunken)]')}
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
              Invite Code
            </label>
            <Input
              type="text"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="enter your invite code"
              disabled={loading}
              className={cn('h-12 text-base font-mono', 'bg-[var(--bg-sunken)]')}
            />
          </div>

          {error && (
            <p className="text-xs text-[var(--color-error)] animate-slide-up flex items-center gap-1.5">
              <span className="w-1 h-1 rounded-full bg-[var(--color-error)]" />
              {error}
            </p>
          )}

          <Button
            variant="accent"
            onClick={handleRegister}
            disabled={loading || !userId.trim() || !password || !confirmPassword || !inviteCode.trim()}
            className="w-full h-12 text-base font-semibold group mt-2"
          >
            {loading ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                <span>Creating account...</span>
              </>
            ) : (
              <>
                <UserPlus className="w-5 h-5" />
                <span>Create Account</span>
              </>
            )}
          </Button>

          {/* Back to login */}
          <Button
            variant="ghost"
            onClick={() => navigate('/login')}
            className="w-full h-11 text-sm"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>Back to Sign In</span>
          </Button>
        </div>

        {/* Footer */}
        <div className="mt-8 pt-6 border-t border-[var(--border-subtle)]">
          <div className="flex items-center justify-center gap-2 text-xs text-[var(--text-tertiary)]">
            <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
            <span>Powered by NetMind.AI</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default RegisterPage;
