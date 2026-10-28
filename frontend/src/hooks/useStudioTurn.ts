/**
 * @file_name: useStudioTurn.ts
 * @author: NetMind.AI
 * @date: 2026-09-03
 * @description: The creation studio's two hooks into the ordinary chat turn.
 *
 * ChatPanel is ~1400 lines of load-bearing streaming logic, so the studio
 * touches it through exactly two calls — `encodeOutgoing` before a send, and
 * `applyFromReply` when a turn settles. Everything else lives here.
 *
 * Why the instruction rides on EVERY turn rather than just the first: the
 * envelope carries the agent's current configuration, and that is what makes
 * the user's own panel edits authoritative — the model revises what the panel
 * holds instead of overwriting it from memory. It also keeps a weaker model
 * emitting the draft block, which a single turn-1 instruction reliably fails
 * to do after a few exchanges (binding rule #15: the platform does not police
 * the user's model choice, so it has to tolerate one).
 *
 * The skill catalogue is fetched while the studio is open. It exists so
 * `mergeAgentDraft` can reject ids that do not exist, and so the panel can
 * install a recommendation by id. Until the fetch lands — or when it fails —
 * the catalogue is UNKNOWN (`null`), which is not the same as empty: an
 * unknown catalogue leaves the accepted recommendations alone instead of
 * filtering every proposed id to nothing and persisting the wipe. A failed
 * fetch is retried, so one marketplace hiccup does not degrade the session.
 *
 * The catalogue is NEVER awaited on the send path. `encodeOutgoing` reads
 * whatever is known right now (the envelope says "unavailable" when nothing
 * is) and kicks off a fetch for the next turn. Awaiting it there made the
 * marketplace an external dependency of the Enter key: while it hung, the
 * composer did not clear and no bubble appeared, and a second Enter sent the
 * same message twice (binding rule #16 — our own downstream must not become
 * a hang the user feels). Applying a settled reply is not on that path, so it
 * may wait for the catalogue — but only up to `CATALOGUE_TIMEOUT_MS`: one
 * fetch is in flight at a time, so a request that never settles would
 * otherwise be the promise every later apply awaits, and every model write
 * for the rest of the session would silently never happen. On timeout the
 * catalogue stays unknown (skill suggestions untouched this turn), the
 * in-flight slot is released and the next call tries again.
 */
import { useCallback, useEffect, useRef } from 'react';
import { api } from '@/lib/api';
import { MARKETPLACE_SEARCH_TIMEOUT_MS } from '@/lib/apiTimeouts';
import { useConfigStore, usePreloadStore, useStudioStore, selectStudioOpen, isStudioOpen } from '@/stores';
import {
  encodeBuilderTurn,
  mergeAgentDraft,
  parseAgentDraft,
  type SkillCatalogue,
} from '@/lib/builderProtocol';
import { applyLiveFields, readCurrentConfig } from '@/lib/builderApply';

/**
 * How many catalogue entries the envelope carries. The envelope TELLS the
 * model when the catalogue is cut ("first N of M"), so this is a page size,
 * not a silent ceiling on what can be recommended.
 */
const CATALOGUE_LIMIT = 60;

/**
 * Upper bound on waiting for the catalogue anywhere it IS awaited. The
 * request itself aborts after `MARKETPLACE_SEARCH_TIMEOUT_MS` (apiTimeouts) — that
 * is the real fix, it frees the connection. This is the second guard, a
 * little later, so a hook-level wait can never outlive the request even if
 * the abort path is bypassed (e.g. a mocked or wrapped transport). Derived,
 * not a second literal: two copies of the number is how they drift.
 */
const CATALOGUE_TIMEOUT_MS = MARKETPLACE_SEARCH_TIMEOUT_MS + 2_000;

function withTimeout<T>(promise: Promise<T>, ms: number, onTimeout: T): Promise<T> {
  return new Promise<T>((resolve) => {
    const timer = setTimeout(() => {
      console.warn(`[studio] skill catalogue: no answer within ${ms}ms; continuing without it`);
      resolve(onTimeout);
    }, ms);
    promise.then(
      (v) => { clearTimeout(timer); resolve(v); },
      (err: unknown) => {
        clearTimeout(timer);
        // The only trace a failed / aborted catalogue fetch leaves — the UI
        // shows nothing (suggestions simply stay as they were), so without
        // this line a 500, a DNS miss or the abort above would be invisible.
        console.warn('[studio] skill catalogue request failed; continuing without it', err);
        resolve(onTimeout);
      },
    );
  });
}

export function useStudioTurn(agentId: string | null) {
  const agents = useConfigStore((s) => s.agents);
  const refreshAgents = useConfigStore((s) => s.refreshAgents);
  const refreshAwareness = usePreloadStore((s) => s.refreshAwareness);
  const setRecommendations = useStudioStore((s) => s.setRecommendations);
  const setApplyError = useStudioStore((s) => s.setApplyError);
  const studioOpen = useStudioStore(selectStudioOpen(agentId));

  // Refs, not state: callbacks read them without being re-created per fetch,
  // and nothing renders from them. `null` = unknown.
  const catalogueRef = useRef<SkillCatalogue | null>(null);
  const catalogueInFlightRef = useRef<Promise<SkillCatalogue | null> | null>(null);

  const loadCatalogue = useCallback((): Promise<SkillCatalogue | null> => {
    if (catalogueRef.current) return Promise.resolve(catalogueRef.current);
    if (catalogueInFlightRef.current) return catalogueInFlightRef.current;
    const attempt = withTimeout(
      api.searchMarketplaceSkills({ limit: CATALOGUE_LIMIT }),
      CATALOGUE_TIMEOUT_MS,
      null,
    )
      .then((res) => {
        if (!res) return null; // timed out or failed: stays unknown
        const items = (res.items ?? []).map((s) => ({ id: s.skill_id, name: s.name }));
        const next: SkillCatalogue = { items, total: Math.max(res.total ?? items.length, items.length) };
        catalogueRef.current = next;
        return next;
      })
      // Only a throw inside the mapping above lands here (withTimeout already
      // turned transport failures into null); the result is the same: unknown.
      .catch(() => null)
      .finally(() => {
        catalogueInFlightRef.current = null;
      });
    catalogueInFlightRef.current = attempt;
    return attempt;
  }, []);

  useEffect(() => {
    if (!studioOpen) return;
    void loadCatalogue();
  }, [studioOpen, loadCatalogue]);

  const identity = useCallback(() => {
    const found = agents.find((a) => a.agent_id === agentId);
    return { name: found?.name ?? '', description: found?.description ?? '' };
  }, [agents, agentId]);

  const recommendationsFor = useCallback(
    (id: string) => useStudioStore.getState().recommendations[id] ?? { skill_ids: [], channels: [] },
    [],
  );

  /**
   * Wrap an outgoing message, or return it untouched when the studio is shut.
   * Returns the original text on any failure — a studio hiccup must never
   * swallow the message the user just wrote.
   */
  const encodeOutgoing = useCallback(
    async (request: string): Promise<string> => {
      if (!agentId || !isStudioOpen(agentId)) return request;
      // Not awaited: the send must not wait on the marketplace (see header).
      void loadCatalogue();
      try {
        const current = await readCurrentConfig(agentId, identity(), recommendationsFor(agentId));
        return encodeBuilderTurn({ request, current, catalogue: catalogueRef.current }) ?? request;
      } catch {
        return request;
      }
    },
    [agentId, identity, recommendationsFor, loadCatalogue],
  );

  /**
   * Apply a settled reply's draft block to the agent.
   *
   * Text fields are written; skills and channels are stored as
   * recommendations for the panel to offer. A parse failure is a no-op by
   * design: "this turn changed no configuration" is a perfectly good outcome
   * and must never interrupt the conversation. Write failures land in the
   * studio store, where the panel shows them as one line — never a modal,
   * never a disabled composer (binding rule #15).
   */
  const applyFromReply = useCallback(
    async (replyText: string): Promise<void> => {
      if (!agentId || !isStudioOpen(agentId)) return;
      const parsed = parseAgentDraft(replyText);
      if (!parsed) return;
      try {
        // Text fields first, and WITHOUT waiting for the catalogue: name,
        // description and instructions do not depend on it, and `prev` is a
        // snapshot — every second spent waiting widens the window in which a
        // panel edit the user just saved is diffed against a stale `prev`
        // and overwritten. The catalogue only gates skill_ids.
        const prev = await readCurrentConfig(agentId, identity(), recommendationsFor(agentId));
        const textOnly = mergeAgentDraft(prev, parsed, null);
        const outcome = await applyLiveFields(agentId, prev, textOnly);
        setApplyError(agentId, outcome.errors.length ? outcome.errors.join('; ') : null);
        // Refresh only what actually changed, so a turn that touched one field
        // does not refetch the other.
        if (outcome.changed.includes('identity')) void refreshAgents();
        if (outcome.changed.includes('awareness')) void refreshAwareness(agentId, true);
        // Channel suggestions do not depend on the catalogue either (they are
        // checked against the compile-time set), so they land now. skill_ids
        // is carried over UNCHANGED here — setRecommendations replaces the
        // whole record, and dropping it would wipe the suggestions accepted
        // on earlier turns.
        setRecommendations(agentId, { skill_ids: prev.skill_ids, channels: textOnly.channels });

        // Then the skill suggestions, once the catalogue is known (bounded
        // wait; unknown → this turn leaves them untouched). The store is
        // reactive, so a turn that ONLY recommended a skill re-renders the
        // panel's suggestions — no refresh of unrelated data.
        const catalogue = await loadCatalogue();
        const withSkills = mergeAgentDraft(
          prev,
          parsed,
          catalogue ? catalogue.items.map((s) => s.id) : null,
        );
        setRecommendations(agentId, { skill_ids: withSkills.skill_ids, channels: textOnly.channels });
      } catch (e) {
        setApplyError(agentId, String(e));
      }
    },
    [agentId, identity, recommendationsFor, loadCatalogue, setRecommendations, setApplyError, refreshAgents, refreshAwareness],
  );

  return { encodeOutgoing, applyFromReply };
}
