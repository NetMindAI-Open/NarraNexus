/**
 * @file SettingsModal.tsx
 * @description Full-screen settings modal (ChatGPT-style) with sidebar navigation.
 *
 * Replaces the small popover with a spacious modal containing:
 *   - Provider Management (add/remove providers)
 *   - Model Assignment (Agent / Embedding / Helper LLM with descriptions)
 *   - Embedding Index Status
 *
 * Each slot section includes a plain-language explanation of what it does and
 * how it affects the Agent's behavior, making it accessible to non-technical users.
 */

import { useState, useEffect, useCallback } from 'react';
import { X, Cpu, Database, Info } from 'lucide-react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';
import { Button, ScrollArea } from '@/components/ui';
import { ProviderSettings } from './ProviderSettings';
import { EmbeddingStatus } from '@/components/ui/EmbeddingStatus';

// =============================================================================
// Sidebar navigation sections
// =============================================================================

interface NavSection {
  id: string;
  label: string;
  icon: typeof Cpu;
}

const NAV_SECTIONS: NavSection[] = [
  { id: 'providers', label: 'LLM Providers', icon: Cpu },
  { id: 'embedding', label: 'Embedding Index', icon: Database },
];

// =============================================================================
// Slot explanation cards (shown above the provider settings)
// =============================================================================

const SLOT_EXPLANATIONS = [
  {
    name: 'Agent',
    color: 'var(--accent-primary)',
    description:
      'The "brain" of your AI agent. This model handles all conversations with users, ' +
      'makes decisions, and executes tasks. A more capable model here means smarter, more nuanced responses.',
    protocol: 'Anthropic protocol',
  },
  {
    name: 'Embedding',
    color: 'var(--color-success)',
    description:
      'Converts text into numerical vectors so the agent can search its memory. ' +
      'This powers "semantic search" — finding relevant past conversations even when the exact words differ. ' +
      'Affects how well the agent remembers context.',
    protocol: 'OpenAI protocol',
  },
  {
    name: 'Helper LLM',
    color: 'var(--color-warning)',
    description:
      'A secondary AI model used for behind-the-scenes analysis: summarizing conversations, ' +
      'extracting key information, and generating internal reports. Does not talk to users directly, ' +
      'but influences the quality of the agent\'s background processing.',
    protocol: 'OpenAI protocol',
  },
];

// =============================================================================
// Props
// =============================================================================

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

// =============================================================================
// Component
// =============================================================================

export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const [activeSection, setActiveSection] = useState('providers');

  // ESC key to close + lock body scroll
  const handleEscape = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleEscape]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-50">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-[rgba(17,18,20,0.6)] animate-fade-in"
        onClick={onClose}
      />

      {/* Modal container */}
      <div className="fixed inset-0 flex items-center justify-center p-6">
        <div
          className={cn(
            'relative w-full max-w-4xl h-[85vh] overflow-hidden',
            'bg-[var(--bg-primary)] border border-[var(--text-primary)]',
            'animate-slide-up',
            'flex flex-col',
          )}
          onClick={(e) => e.stopPropagation()}
        >

          {/* ─── Header ─── */}
          <div className="relative flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] shrink-0">
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Settings</h2>
            <Button
              variant="ghost"
              size="icon"
              onClick={onClose}
              className="w-8 h-8 hover:bg-[var(--bg-tertiary)]"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>

          {/* ─── Body: sidebar + content ─── */}
          <div className="relative flex flex-1 min-h-0">
            {/* Sidebar */}
            <nav className="w-48 shrink-0 border-r border-[var(--border-subtle)] py-3 px-2">
              {NAV_SECTIONS.map((section) => {
                const Icon = section.icon;
                const isActive = activeSection === section.id;

                return (
                  <button
                    key={section.id}
                    onClick={() => setActiveSection(section.id)}
                    className={cn(
                      'w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors',
                      isActive
                        ? 'bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] font-medium'
                        : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]',
                    )}
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    {section.label}
                  </button>
                );
              })}
            </nav>

            {/* Content area */}
            <ScrollArea className="flex-1" viewportClassName="p-6">
            <div>
              {/* ─── LLM Providers Section ─── */}
              {activeSection === 'providers' && (
                <div className="space-y-6 max-w-2xl">
                  {/* Slot explanation cards */}
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <Info className="w-4 h-4 text-[var(--text-tertiary)]" />
                      <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                        What are these model slots?
                      </h3>
                    </div>
                    <p className="text-xs text-[var(--text-tertiary)] mb-4">
                      NarraNexus uses three AI models for different purposes. You can use the same provider
                      for all three, or mix and match to optimize for cost, speed, or quality.
                    </p>
                    <div className="grid grid-cols-1 gap-3">
                      {SLOT_EXPLANATIONS.map((slot) => (
                        <div
                          key={slot.name}
                          className="p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]"
                        >
                          <div className="flex items-center gap-2 mb-1">
                            <div
                              className="w-2 h-2 rounded-full"
                              style={{ backgroundColor: slot.color }}
                            />
                            <span className="text-sm font-medium text-[var(--text-primary)]">
                              {slot.name}
                            </span>
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--bg-primary)] text-[var(--text-tertiary)]">
                              {slot.protocol}
                            </span>
                          </div>
                          <p className="text-xs text-[var(--text-tertiary)] leading-relaxed ml-4">
                            {slot.description}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Divider */}
                  <div className="border-t border-[var(--border-subtle)]" />

                  {/* Provider Settings (reused from existing component) */}
                  <ProviderSettings />
                </div>
              )}

              {/* ─── Embedding Index Section ─── */}
              {activeSection === 'embedding' && (
                <div className="space-y-4 max-w-2xl">
                  <div>
                    <h3 className="text-sm font-medium text-[var(--text-primary)] mb-2">
                      Vector Embedding Index
                    </h3>
                    <p className="text-xs text-[var(--text-tertiary)] leading-relaxed">
                      The embedding index converts your agent's memories into searchable vectors.
                      When you change the embedding model, existing vectors need to be rebuilt
                      to match the new model's format. This process runs in the background
                      and does not interrupt normal agent operations.
                    </p>
                  </div>

                  <EmbeddingStatus />
                </div>
              )}
            </div>
            </ScrollArea>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
