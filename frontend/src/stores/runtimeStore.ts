/**
 * @file_name: runtimeStore.ts
 * @author: NexusAgent
 * @date: 2026-04-02
 * @description: Runtime configuration store
 *
 * Manages app mode, user type, and derived feature flags.
 * Persists mode, userType, and initialized state to localStorage.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AppMode, UserType, FeatureFlags } from '@/types/platform';

interface RuntimeState {
  mode: AppMode | null;
  userType: UserType;
  features: FeatureFlags;
  initialized: boolean;

  setMode: (mode: AppMode | null) => void;
  setUserType: (type: UserType) => void;
  initialize: () => void;
}

function deriveFeatures(
  mode: AppMode | null,
  userType: UserType,
  initialized: boolean,
): FeatureFlags {
  if (mode === 'local') {
    return {
      canUseClaudeCode: true,
      canUseApiMode: true,
      showSystemPage: true,
      showSetupWizard: !initialized,
    };
  }

  if (userType === 'internal') {
    return {
      canUseClaudeCode: true,
      canUseApiMode: true,
      showSystemPage: false,
      showSetupWizard: false,
    };
  }

  // Cloud + external
  return {
    canUseClaudeCode: false,
    canUseApiMode: true,
    showSystemPage: false,
    showSetupWizard: false,
  };
}

export const useRuntimeStore = create<RuntimeState>()(
  persist(
    (set, get) => ({
      mode: null,
      userType: 'internal',
      initialized: false,
      features: deriveFeatures(null, 'internal', false),

      setMode: (mode) => {
        const { userType, initialized } = get();
        set({ mode, features: deriveFeatures(mode, userType, initialized) });
      },

      setUserType: (userType) => {
        const { mode, initialized } = get();
        set({
          userType,
          features: deriveFeatures(mode, userType, initialized),
        });
      },

      initialize: () => {
        const { mode, userType } = get();
        set({
          initialized: true,
          features: deriveFeatures(mode, userType, true),
        });
      },
    }),
    {
      name: 'narranexus-runtime',
      partialize: (state) => ({
        mode: state.mode,
        userType: state.userType,
        initialized: state.initialized,
      }),
      merge: (persisted, current) => {
        const p = persisted as Partial<RuntimeState>;
        const mode = p.mode ?? current.mode;
        const userType = p.userType ?? current.userType;
        const initialized = p.initialized ?? current.initialized;
        return {
          ...current,
          mode,
          userType,
          initialized,
          features: deriveFeatures(mode, userType, initialized),
        };
      },
    },
  ),
);
