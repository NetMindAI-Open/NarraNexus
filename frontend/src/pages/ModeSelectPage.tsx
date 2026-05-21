/**
 * ModeSelectPage · NM Design System (M3 Wave 6)
 *
 * First-launch mode selection page. Two PaperCards for Local vs Cloud,
 * with NM BracketMarkLogo brand header and species-colored hover accents.
 */

import { useNavigate } from 'react-router-dom';
import { Monitor, Cloud } from 'lucide-react';
import { useRuntimeStore } from '@/stores/runtimeStore';
import { useTheme } from '@/hooks';
import { cn } from '@/lib/utils';
import { PaperCard } from '@/components/nm';

// Hardcoded cloud endpoint for locally-built clients (Tauri desktop, dev).
const DEFAULT_CLOUD_URL = 'https://agent.narra.nexus/';

interface ModeCardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  /** Species tint applied to icon ring + hover edge */
  species: 'carbon' | 'silicon';
  onClick: () => void;
}

function ModeCard({ icon, title, description, species, onClick }: ModeCardProps) {
  const ringColor = species === 'carbon' ? 'var(--color-carbon)' : 'var(--color-silicon)';
  return (
    <button
      onClick={onClick}
      className={cn(
        'group relative flex flex-col items-center gap-5 p-10',
        'w-80 transition-all duration-200',
      )}
    >
      <PaperCard
        padding="lg"
        className="w-full h-full flex flex-col items-center gap-5 hover:bg-[color:var(--nm-raised)] transition-colors"
        style={{
          borderColor: 'var(--nm-hairline)',
        }}
      >
        {/* Ring around icon — species-colored */}
        <div
          className="w-16 h-16 rounded-full flex items-center justify-center transition-colors"
          style={{
            border: `2px solid ${ringColor}`,
            color: 'var(--nm-ink)',
          }}
        >
          {icon}
        </div>

        <div className="text-center space-y-2">
          <h3
            className="text-lg font-bold tracking-tight"
            style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
          >
            {title}
          </h3>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--nm-ink70)' }}>
            {description}
          </p>
        </div>
      </PaperCard>
    </button>
  );
}

export function ModeSelectPage() {
  const navigate = useNavigate();
  const { isDark } = useTheme();
  const { setMode, setCloudApiUrl } = useRuntimeStore();

  const handleLocal = () => {
    setMode('local');
    navigate('/login');
  };

  const handleCloudSelect = () => {
    setCloudApiUrl(DEFAULT_CLOUD_URL.replace(/\/+$/, ''));
    setMode('cloud-app');
    navigate('/login');
  };

  return (
    <div
      className="h-screen w-screen flex flex-col items-center justify-center gap-12"
      style={{ background: 'var(--nm-paper)' }}
    >
      {/* Brand header — original logo preserved */}
      <div className="flex flex-col items-center gap-4 animate-fade-in">
        <img
          src={isDark ? '/logo-dark-mode.svg' : '/logo-light-mode.svg'}
          alt="NarraNexus"
          className="h-20 w-auto object-contain"
        />
        <h1
          className="text-3xl font-bold tracking-tight"
          style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
        >
          Welcome
        </h1>
        <div
          className="text-[10px] uppercase tracking-[0.22em]"
          style={{ fontFamily: 'var(--font-mono)', color: 'var(--nm-ink50)' }}
        >
          Choose your runtime
        </div>
      </div>

      <div className="flex gap-8 animate-fade-in" style={{ animationDelay: '0.1s' }}>
        <ModeCard
          icon={<Monitor className="w-7 h-7" style={{ color: 'var(--color-carbon)' }} />}
          title="Local Mode"
          description="Everything runs on your machine. Your data stays local. Offline capable."
          species="carbon"
          onClick={handleLocal}
        />
        <ModeCard
          icon={<Cloud className="w-7 h-7" style={{ color: 'var(--color-silicon)' }} />}
          title="Cloud Mode"
          description="Connect to the managed NetMind.AI cloud. Access from any device."
          species="silicon"
          onClick={handleCloudSelect}
        />
      </div>
    </div>
  );
}

export default ModeSelectPage;
