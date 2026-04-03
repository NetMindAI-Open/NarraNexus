/**
 * @file_name: SetupPage.tsx
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Local-mode setup wizard
 *
 * Three-step wizard:
 * 1. Initialize — auto-runs database setup (simulated)
 * 2. Configure — inline SettingsPage for model provider config
 * 3. Ready — confirmation with "Get Started" button
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Loader2,
  CheckCircle2,
  Database,
  Settings,
  Rocket,
} from 'lucide-react';
import { Button } from '@/components/ui';
import { SettingsPage } from '@/pages/SettingsPage';
import { useRuntimeStore } from '@/stores/runtimeStore';
import { cn } from '@/lib/utils';

type Step = 1 | 2 | 3;

const STEPS = [
  { num: 1 as Step, label: 'Initialize', icon: Database },
  { num: 2 as Step, label: 'Configure', icon: Settings },
  { num: 3 as Step, label: 'Ready', icon: Rocket },
];

export function SetupPage() {
  const navigate = useNavigate();
  const { initialize } = useRuntimeStore();

  const [currentStep, setCurrentStep] = useState<Step>(1);
  const [initDone, setInitDone] = useState(false);

  // Step 1: auto-run initialization
  useEffect(() => {
    if (currentStep !== 1) return;
    const timer = setTimeout(() => {
      setInitDone(true);
      // Auto-advance after a brief pause
      setTimeout(() => setCurrentStep(2), 500);
    }, 2000);
    return () => clearTimeout(timer);
  }, [currentStep]);

  const handleGetStarted = () => {
    initialize();
    navigate('/login');
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-[var(--bg-deep)]">
      {/* Step indicator */}
      <div className="flex items-center justify-center gap-2 pt-8 pb-6">
        {STEPS.map((step, i) => {
          const StepIcon = step.icon;
          const isActive = currentStep === step.num;
          const isComplete = currentStep > step.num;
          return (
            <div key={step.num} className="flex items-center">
              {i > 0 && (
                <div
                  className={cn(
                    'w-12 h-px mx-2 transition-colors duration-300',
                    isComplete
                      ? 'bg-[var(--accent-primary)]'
                      : 'bg-[var(--border-default)]',
                  )}
                />
              )}
              <div className="flex items-center gap-2">
                <div
                  className={cn(
                    'w-8 h-8 rounded-full flex items-center justify-center transition-all duration-300 text-xs font-mono',
                    isActive &&
                      'bg-[var(--accent-primary)] text-[var(--text-inverse)] dark:text-[var(--bg-deep)] shadow-[0_0_12px_var(--accent-glow)]',
                    isComplete &&
                      'bg-[var(--accent-primary)] text-[var(--text-inverse)] dark:text-[var(--bg-deep)]',
                    !isActive &&
                      !isComplete &&
                      'bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] border border-[var(--border-default)]',
                  )}
                >
                  {isComplete ? (
                    <CheckCircle2 className="w-4 h-4" />
                  ) : (
                    <StepIcon className="w-4 h-4" />
                  )}
                </div>
                <span
                  className={cn(
                    'text-sm font-medium transition-colors duration-300',
                    isActive
                      ? 'text-[var(--text-primary)]'
                      : 'text-[var(--text-tertiary)]',
                  )}
                >
                  {step.label}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Step content */}
      <div className="flex-1 overflow-y-auto">
        {/* Step 1: Initialize */}
        {currentStep === 1 && (
          <div className="flex flex-col items-center justify-center h-full gap-6 animate-fade-in">
            {!initDone ? (
              <>
                <Loader2 className="w-12 h-12 text-[var(--accent-primary)] animate-spin" />
                <div className="text-center space-y-2">
                  <h2 className="text-xl font-semibold text-[var(--text-primary)]">
                    Setting up local database...
                  </h2>
                  <p className="text-sm text-[var(--text-secondary)]">
                    Initializing SQLite and creating tables
                  </p>
                </div>
              </>
            ) : (
              <>
                <CheckCircle2 className="w-12 h-12 text-[var(--color-success)]" />
                <h2 className="text-xl font-semibold text-[var(--text-primary)]">
                  Database ready
                </h2>
              </>
            )}
          </div>
        )}

        {/* Step 2: Configure */}
        {currentStep === 2 && (
          <div className="flex flex-col items-center animate-fade-in">
            <div className="w-full max-w-2xl">
              <SettingsPage />
            </div>
            <div className="py-6">
              <Button variant="accent" onClick={() => setCurrentStep(3)}>
                Next
              </Button>
            </div>
          </div>
        )}

        {/* Step 3: Ready */}
        {currentStep === 3 && (
          <div className="flex flex-col items-center justify-center h-full gap-6 animate-fade-in">
            <div className="relative">
              <Rocket className="w-16 h-16 text-[var(--accent-primary)]" />
              <div className="absolute -inset-4 rounded-full bg-[var(--accent-primary)] opacity-10 blur-xl -z-10" />
            </div>
            <div className="text-center space-y-2">
              <h2 className="text-2xl font-bold text-[var(--text-primary)]">
                NarraNexus is ready!
              </h2>
              <p className="text-sm text-[var(--text-secondary)]">
                Your local environment is configured and ready to go.
              </p>
            </div>
            <Button variant="accent" size="lg" onClick={handleGetStarted}>
              Get Started
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

export default SetupPage;
