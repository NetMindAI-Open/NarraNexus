/**
 * @file_name: BuilderConfigPanel.tsx
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The creation studio's configuration panel — the right-hand
 * half of "describe what you want, watch the panel fill in".
 *
 * Everything here reflects the REAL agent, not a draft. The studio runs on the
 * agent the user just created, so name and awareness are read from the agent
 * and written straight back; there is no staging area to reconcile and no
 * "apply" step. That is the whole point of the path.
 *
 * Layout follows the Owner's 2026-09-03 reference: identity is NAME ONLY (no
 * avatar — the project has no agent-avatar capability, sidebar avatars are
 * generated from identity; no description field — it is machine-facing copy
 * that other agents read for routing, so the conversation writes it and the
 * Agent Profile page displays it). The instruction box is labelled with the
 * field it actually writes, `awareness`, rather than a synonym.
 *
 * Skills and Channels embed the SAME sections the drawer's own Skills and
 * Channels tabs render. Reuse over re-derivation: a second channel-status
 * reader or skill list would drift from those tabs the first time either
 * changes. The cost is that this tab pulls their lazy chunks too — accepted,
 * because the Owner wants both configurable without leaving the studio.
 *
 * The conversation's SUGGESTIONS sit above each real section. Suggesting and
 * installing stay separate on purpose: installing a skill copies files into
 * the agent's workspace (a model that changes its mind would install then
 * uninstall in front of the user), and binding a channel needs a credential,
 * which is the user's to paste and must never reach the model.
 *
 * Text fields save on BLUR, not per keystroke: a PUT per character would race
 * the model's own writes on the same fields.
 */
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Plus } from 'lucide-react';
import { api } from '@/lib/api';
import { Button, Input, Textarea, ScrollArea } from '@/components/ui';
import { AwarenessPanel } from '@/components/awareness';
import { SkillsPanel } from '@/components/skills';
import {
  useConfigStore,
  usePreloadStore,
  useStudioStore,
  selectRecommendations,
} from '@/stores';
import { AGENT_TEXT_MAX_LENGTH } from '@/lib/agentLimits';

interface BuilderConfigPanelProps {
  agentId: string;
}

export function BuilderConfigPanel({ agentId }: BuilderConfigPanelProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const agents = useConfigStore((s) => s.agents);
  const refreshAgents = useConfigStore((s) => s.refreshAgents);
  const awareness = usePreloadStore((s) => s.awareness);
  const refreshAwareness = usePreloadStore((s) => s.refreshAwareness);
  // Reactive: a turn that only recommended a skill re-renders this section
  // even though no text field (and therefore no other subscription) changed.
  const recommendations = useStudioStore(selectRecommendations(agentId));
  const finishStudio = useStudioStore((s) => s.finishStudio);
  // A model-driven write that failed. The conversation was not interrupted
  // (binding rule #15); this line is where the user finds out.
  const applyError = useStudioStore((s) => s.applyError[agentId] ?? null);

  const agent = agents.find((a) => a.agent_id === agentId);

  // Local mirrors of the two live fields. The server value wins whenever it
  // changes underneath — that is how the model's writes appear here; typing
  // overrides locally until blur commits or the next server value lands.
  const [name, setName] = useState(agent?.name ?? '');
  const [instructions, setInstructions] = useState(awareness ?? '');
  const [saving, setSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [installing, setInstalling] = useState<string | null>(null);
  const [installed, setInstalled] = useState<string[]>([]);
  // One slot per writer, not one shared string: the two field commits and a
  // skill install all report here, and a shared slot let a later `null` from
  // one writer erase another's failure — or a stale manual error hide a fresh
  // one. Each writer owns exactly its own slot.
  type ErrorSlot = 'name' | 'awareness' | 'install';
  const [errors, setErrors] = useState<Partial<Record<ErrorSlot, string>>>({});
  const setSlotError = useCallback((slot: ErrorSlot, message: string | null) => {
    setErrors((prev) => {
      const next = { ...prev };
      if (message) next[slot] = message;
      else delete next[slot];
      return next;
    });
  }, []);

  useEffect(() => setName(agent?.name ?? ''), [agent?.name]);
  useEffect(() => setInstructions(awareness ?? ''), [awareness]);

  /** Returns whether the field is now persisted (a no-op counts as success). */
  const commitName = useCallback(async (): Promise<boolean> => {
    // Clear this writer's slot FIRST — before the early returns too — so a
    // stale "name rejected" line disappears once the field is back in sync.
    setSlotError('name', null);
    const next = name.trim();
    // An empty name is never committed — the same rule the model's path
    // applies in mergeAgentDraft, so a blank field reads as "not changing it"
    // from either side rather than wiping the agent's name. The field snaps
    // back to the real name too: leaving it blank would look like the name
    // WAS cleared, the opposite of what just happened.
    if (!next) {
      setName(agent?.name ?? '');
      return true;
    }
    if (next === (agent?.name ?? '')) return true;
    setSaving(true);
    try {
      const res = await api.updateAgent(agentId, next, agent?.description ?? '');
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      await refreshAgents();
      return true;
    } catch (e) {
      setSlotError('name', String(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [name, agent?.name, agent?.description, agentId, refreshAgents, setSlotError]);

  const commitAwareness = useCallback(async (): Promise<boolean> => {
    setSlotError('awareness', null);
    if (instructions === (awareness ?? '')) return true;
    setSaving(true);
    try {
      const res = await api.updateAwareness(agentId, instructions);
      if (!res.success) throw new Error(res.message ?? res.error ?? 'update failed');
      await refreshAwareness(agentId, true);
      return true;
    } catch (e) {
      setSlotError('awareness', String(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [instructions, awareness, agentId, refreshAwareness, setSlotError]);

  /**
   * Flush pending field edits, then end the studio — in that order, and only
   * if the flush succeeded.
   *
   * A textarea's onBlur fires BEFORE the button's onClick, so a user who types
   * and then clicks Done has a commit in flight at click time. Awaiting both
   * commits here (each a no-op when unchanged) means the last thing they typed
   * is persisted before the studio ends, instead of racing it.
   *
   * A FAILED flush must not end the studio: ending it unmounts this panel (and
   * its error line) and collapses the drawer in the same tick, so the user
   * would see a clean close and believe the edit was saved — while the studio
   * is now unrecoverable and the text never landed. Instead the panel stays,
   * the error line is visible, and Done can be pressed again. Same rule as
   * every other write here: failure leaves the user where they can see it
   * (binding rule #15 — no modal, no disabled composer).
   */
  const finish = useCallback(async () => {
    setFinishing(true);
    let flushed = false;
    try {
      // BOTH run, always — no `&&` short-circuit. They hit two independent
      // endpoints; a rejected name is no reason not to try the instructions,
      // whose only copy is this component's state and would otherwise be
      // lost the moment the panel unmounts, with no error ever shown for it.
      const nameOk = await commitName();
      const awarenessOk = await commitAwareness();
      flushed = nameOk && awarenessOk;
    } finally {
      setFinishing(false);
    }
    if (!flushed) return;
    // Done ENDS the studio (flag, recommendations, resumability). Nothing
    // here touches the drawer: once this agent is neither open nor
    // resumable, useStudioLifecycle collapses the drawer itself — the same
    // way it does for the X, another tab or another agent. Asking the
    // drawer to toggle from here as well used to race that effect (React
    // batches both into one commit; the toggle read the already-collapsed
    // tab as "not open" and re-opened it on an empty panel).
    finishStudio(agentId);
  }, [commitName, commitAwareness, agentId, finishStudio]);

  const install = useCallback(
    async (skillId: string) => {
      setInstalling(skillId);
      setSlotError('install', null);
      try {
        await api.installMarketplaceSkill(skillId, agentId);
        setInstalled((prev) => [...prev, skillId]);
        // The embedded Skills section reads the same list through react-query.
        await qc.invalidateQueries({ queryKey: ['skills'] });
      } catch (e) {
        setSlotError('install', String(e));
      } finally {
        setInstalling(null);
      }
    },
    [agentId, qc, setSlotError],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-8 px-4 py-4">
          <p className="text-xs leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
            {t('builder.panel.intro')}
          </p>

          {/* ── Identity: name only ── */}
          <Section title={t('builder.panel.identity')} hint={t('builder.panel.identityHint')}>
            <FieldCard>
              <label className="block space-y-1.5 p-3">
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {t('builder.panel.name')}
                </span>
                <Input
                  value={name}
                  maxLength={AGENT_TEXT_MAX_LENGTH}
                  placeholder={t('builder.panel.namePlaceholder')}
                  onChange={(e) => setName(e.target.value)}
                  onBlur={() => void commitName()}
                />
              </label>
            </FieldCard>
          </Section>

          {/* ── Behaviour: the awareness field, named after what it writes ── */}
          <Section title={t('builder.panel.behaviour')} hint={t('builder.panel.behaviourHint')}>
            <FieldCard>
              <label className="block space-y-1.5 p-3">
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                  {t('builder.panel.awareness')}
                </span>
                <Textarea
                  rows={12}
                  value={instructions}
                  placeholder={t('builder.panel.awarenessPlaceholder')}
                  onChange={(e) => setInstructions(e.target.value)}
                  onBlur={() => void commitAwareness()}
                  className="font-mono text-[12px] leading-relaxed"
                />
              </label>
            </FieldCard>
          </Section>

          {/* ── Skills: suggestions, then the real section ── */}
          <Section
            title={t('builder.panel.skills')}
            hint={t('builder.panel.skillsHint')}
            optional={t('builder.panel.optional')}
          >
            {recommendations.skill_ids.length > 0 && (
              <div className="space-y-2">
                <SuggestionLabel>{t('builder.panel.suggested')}</SuggestionLabel>
                {recommendations.skill_ids.map((skillId) => {
                  const done = installed.includes(skillId);
                  return (
                    <div
                      key={skillId}
                      className="flex items-center gap-2 rounded-[var(--radius-lg)] px-3 py-2"
                      style={{ border: '1px solid var(--nm-hairline)', background: 'var(--bg-primary)' }}
                    >
                      <span
                        className="min-w-0 flex-1 truncate text-[12px]"
                        style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}
                      >
                        {skillId}
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={done || installing === skillId}
                        onClick={() => void install(skillId)}
                        className="gap-1.5 shrink-0"
                      >
                        {installing === skillId ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : done ? (
                          <Check className="h-3 w-3" />
                        ) : (
                          <Plus className="h-3 w-3" />
                        )}
                        {done ? t('builder.panel.installed') : t('builder.panel.install')}
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
            <EmbeddedSection height="240px">
              <SkillsPanel embedded compact section="skills" />
            </EmbeddedSection>
          </Section>

          {/* ── Channels: suggestions, then the real section ── */}
          <Section
            title={t('builder.panel.channels')}
            hint={t('builder.panel.channelsHint')}
            optional={t('builder.panel.optional')}
          >
            {recommendations.channels.length > 0 && (
              <div className="space-y-2">
                <SuggestionLabel>{t('builder.panel.suggested')}</SuggestionLabel>
                <div className="flex flex-wrap gap-1.5">
                  {recommendations.channels.map((channel) => (
                    <span
                      key={channel}
                      className="rounded-full px-2.5 py-1 text-[11px]"
                      style={{
                        fontFamily: 'var(--font-mono)',
                        border: '1px solid var(--nm-hairline)',
                        background: 'var(--nm-paper-warm)',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {channel}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {/* The credential is pasted here, user → backend. It never enters
                the conversation envelope. */}
            <EmbeddedSection height="300px">
              <AwarenessPanel embedded section="channels" />
            </EmbeddedSection>
          </Section>

          {/* One line per writer (model-driven first): a shared slot let one
              writer's `null` erase another's failure, or an old error hide a
              fresh one. */}
          {applyError && (
            <p className="text-[11px] leading-relaxed" style={{ color: 'var(--color-error)' }}>
              {applyError}
            </p>
          )}
          {(['name', 'awareness', 'install'] as const).map((slot) =>
            errors[slot] ? (
              <p key={slot} className="text-[11px] leading-relaxed" style={{ color: 'var(--color-error)' }}>
                {errors[slot]}
              </p>
            ) : null,
          )}
        </div>
      </ScrollArea>

      <div
        className="flex items-center justify-between gap-3 border-t px-4 py-3"
        style={{ borderColor: 'var(--nm-hairline)' }}
      >
        <span className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
          {saving ? t('builder.panel.saving') : t('builder.panel.savedHint')}
        </span>
        {/* No "discard": every field here is already written to the agent, so
            there is nothing to roll back. Finishing only leaves the studio —
            the drawer close is owned by the caller that opened it.

            Disabled ONLY while flushing this panel's own field writes. A skill
            that is installing or studying must NOT block it: study runs for
            minutes inside the agent's workspace and keeps running after the
            studio closes, so gating on it would strand the user in a panel
            waiting for something that does not need them. */}
        <Button size="sm" disabled={finishing} onClick={() => void finish()}>
          {finishing ? t('builder.panel.saving') : t('builder.panel.done')}
        </Button>
      </div>
    </div>
  );
}

function Section({
  title,
  hint,
  optional,
  children,
}: {
  title: string;
  hint?: string;
  /** Marks a section the user can skip entirely — Skills and Channels are
   *  both optional, and saying so stops the panel reading as a checklist. */
  optional?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2.5">
      <div>
        <h3
          className="flex items-center gap-2 text-[13px] font-semibold"
          style={{ color: 'var(--text-primary)' }}
        >
          {title}
          {optional && (
            <span
              className="rounded-full px-2 py-0.5 text-[9.5px] font-medium uppercase tracking-[0.1em]"
              style={{
                fontFamily: 'var(--font-mono)',
                border: '1px solid var(--nm-hairline)',
                color: 'var(--text-tertiary)',
              }}
            >
              {optional}
            </span>
          )}
        </h3>
        {hint && (
          <p className="mt-0.5 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
            {hint}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

function FieldCard({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-[var(--radius-xl)] overflow-hidden"
      style={{ border: '1px solid var(--nm-hairline)', background: 'var(--bg-primary)' }}
    >
      {children}
    </div>
  );
}

/**
 * Hosts a reused drawer section without its own outer card chrome.
 *
 * The height is EXPLICIT because those panels are built for a full drawer
 * column: they use `flex-1 min-h-0` + an inner ScrollArea, which needs a
 * bounded parent. Left to grow, Skills alone would push Channels off the
 * bottom of the studio's scroll.
 */
function EmbeddedSection({ height, children }: { height: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-[var(--radius-xl)] overflow-hidden flex flex-col"
      style={{ border: '1px solid var(--nm-hairline)', background: 'var(--bg-primary)', height }}
    >
      {children}
    </div>
  );
}

function SuggestionLabel({ children }: { children: React.ReactNode }) {
  return (
    <span
      className="block text-[10px] font-medium uppercase tracking-[0.13em]"
      style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}
    >
      {children}
    </span>
  );
}
