/**
 * Register Page · NM Design System (M3 Wave 2)
 * Cloud mode only, requires invite code.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UserPlus, Cloud, ArrowLeft } from 'lucide-react';
import { useConfigStore, useRuntimeStore } from '@/stores';
import { useTheme } from '@/hooks';
import { api } from '@/lib/api';
import {
  Button,
  FormField,
  TextInput,
  Chip,
  Divider,
  PaperCard,
} from '@/components/nm';

export function RegisterPage() {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [welcomeQuota, setWelcomeQuota] = useState<{
    input: number;
    output: number;
  } | null>(null);

  const navigate = useNavigate();
  const { isDark } = useTheme();
  const { login, setAgents, setAgentId } = useConfigStore();
  const mode = useRuntimeStore((s) => s.mode);
  const setMode = useRuntimeStore((s) => s.setMode);
  const setCloudApiUrl = useRuntimeStore((s) => s.setCloudApiUrl);

  const canChangeMode = mode !== 'cloud-web';
  const handleChangeMode = () => {
    setCloudApiUrl('');
    setMode(null);
    navigate('/mode-select');
  };

  const handleRegister = async () => {
    setError('');
    if (!userId.trim()) { setError('Please enter a username'); return; }
    if (userId.trim().length < 2 || userId.trim().length > 32) { setError('Username must be 2-32 characters'); return; }
    if (!password || password.length < 6) { setError('Password must be at least 6 characters'); return; }
    if (password !== confirmPassword) { setError('Passwords do not match'); return; }
    if (!inviteCode.trim()) { setError('Please enter the invite code'); return; }

    setLoading(true);
    try {
      const res = await api.register(userId.trim(), password, inviteCode.trim());
      if (!res.success) {
        setError(res.error || 'Registration failed');
        setLoading(false);
        return;
      }
      login(userId.trim(), res.token || undefined, 'user');

      try {
        const agentsRes = await api.getAgents();
        if (agentsRes.success && agentsRes.agents.length > 0) {
          setAgents(agentsRes.agents);
          setAgentId(agentsRes.agents[0].agent_id);
        }
      } catch {
        // empty agents is fine for new account
      }

      const isCloud = mode === 'cloud-app' || mode === 'cloud-web';
      if (isCloud && res.has_system_quota) {
        setWelcomeQuota({
          input: res.initial_input_tokens ?? 0,
          output: res.initial_output_tokens ?? 0,
        });
        setTimeout(() => navigate('/'), 1800);
      } else {
        navigate('/');
      }
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
            className="flex items-center gap-1.5 text-[11px] mb-6 -mt-2 opacity-60 hover:opacity-100 transition-opacity"
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
            Create account · Invite required
          </div>
          <Chip species="silicon" leading={<Cloud className="w-3 h-3" />}>
            Cloud mode
          </Chip>
        </div>

        <Divider />

        <div className="space-y-4 mt-6">
          <FormField label="Username">
            <TextInput
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="choose_a_username"
              disabled={loading}
              autoFocus
              className="h-12"
            />
          </FormField>

          <FormField label="Password" hint="At least 6 characters.">
            <TextInput
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="••••••••"
              disabled={loading}
              className="h-12"
            />
          </FormField>

          <FormField label="Confirm Password">
            <TextInput
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="••••••••"
              disabled={loading}
              className="h-12"
            />
          </FormField>

          <FormField label="Invite Code">
            <TextInput
              type="text"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="enter your invite code"
              disabled={loading}
              className="h-12"
            />
          </FormField>

          <p className="text-xs -mt-1" style={{ color: 'var(--nm-ink50)' }}>
            No invite code?{' '}
            <a
              href="https://website.narra.nexus/invite"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
              style={{ color: 'var(--nm-ink70)' }}
            >
              Request one here
            </a>{' '}
            &mdash; we&rsquo;ll email you a code. (Check your spam folder if it
            doesn&rsquo;t arrive.)
          </p>

          {error && (
            <p
              className="text-xs animate-slide-up flex items-center gap-1.5"
              role="alert"
              style={{ color: 'var(--color-error)' }}
            >
              <span
                className="w-1 h-1 rounded-full inline-block"
                style={{ background: 'var(--color-error)' }}
              />
              {error}
            </p>
          )}

          {welcomeQuota && (
            <PaperCard padding="md" className="animate-slide-up" style={{ borderColor: 'var(--color-carbon)' }}>
              <div className="text-sm font-medium mb-1" style={{ color: 'var(--nm-ink)' }}>
                Welcome! You've got starter credits.
              </div>
              <div className="text-xs" style={{ color: 'var(--nm-ink70)' }}>
                {welcomeQuota.input.toLocaleString()} input tokens · {welcomeQuota.output.toLocaleString()} output tokens on the system provider. Taking you to the dashboard…
              </div>
            </PaperCard>
          )}

          <Button
            variant="primary"
            size="lg"
            onClick={handleRegister}
            disabled={loading || !userId.trim() || !password || !confirmPassword || !inviteCode.trim()}
            loading={loading}
            className="w-full mt-2"
            leading={!loading ? <UserPlus className="w-4 h-4" /> : undefined}
          >
            {loading ? 'Creating account…' : 'Create Account'}
          </Button>

          <Button
            variant="ghost"
            onClick={() => navigate('/login')}
            className="w-full"
            leading={<ArrowLeft className="w-4 h-4" />}
          >
            Back to Sign In
          </Button>
        </div>

        <div className="mt-8 pt-6 border-t" style={{ borderColor: 'var(--nm-hairline)' }}>
          <div
            className="flex items-center justify-center gap-2 text-xs"
            style={{ color: 'var(--nm-ink50)', fontFamily: 'var(--font-mono)' }}
          >
            <span
              className="w-1.5 h-1.5 rounded-full inline-block"
              style={{ background: 'var(--color-silicon)' }}
              aria-hidden
            />
            <span>Powered by NetMind.AI</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default RegisterPage;
