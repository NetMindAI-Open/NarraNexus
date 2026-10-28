/**
 * @file_name: gates.tsx
 * @author: Bin Liang
 * @date: 2026-09-03
 * @description: Lazy gates the loader registers for a plugin's declared pages/panels/renderers/artifact kinds: they fire the activation event, then render what the plugin registered.
 */
import { useEffect, useSyncExternalStore, type ComponentType } from 'react';

import type { Artifact } from '@/types/artifact';

import { activationState, fireActivation, subscribeActivation } from './activation';
import { PluginStatus as Status } from './PluginStatus';
import {
  AGENT_CARD_BADGES,
  ARTIFACT_KINDS,
  COMPOSER_EXTENSIONS,
  MESSAGE_RENDERERS,
  PAGES,
  PANELS,
  SIDEBAR_SECTIONS,
  TIMELINE_EVENTS,
  TOP_BAR_ITEMS,
  type KindDescriptor,
  type MessageRendererDef,
  type MessageRendererProps,
  type PanelProps,
  type SlotComponentProps,
  type TimelineEventProps,
} from './registries';

function useActivation(pluginId: string) {
  return useSyncExternalStore(subscribeActivation, () => activationState(pluginId), () => activationState(pluginId));
}

export function makePageGate(pluginId: string, pageId: string): ComponentType<Record<string, never>> {
  const Gate = () => {
    const { state, error } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onPage:${pageId}`);
    }, []);
    const entry = PAGES.list().find((e) => e.id === pageId && e.owner === pluginId && e.value.element !== null && e.value.element !== Gate);
    if (state === 'active' && entry?.value.element) {
      const Real = entry.value.element;
      return <Real />;
    }
    return <Status pluginId={pluginId} state={state} error={error} />;
  };
  Gate.displayName = `PluginPageGate(${pageId})`;
  return Gate;
}

export function makePanelGate(pluginId: string, panelId: string): ComponentType<PanelProps> {
  const Gate = (props: PanelProps) => {
    const { state, error } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onPanel:${panelId}`);
    }, []);
    const entry = PANELS.list().find((e) => e.id === panelId && e.owner === pluginId && e.value.component !== Gate);
    if (state === 'active' && entry) {
      const Real = entry.value.component;
      return <Real {...props} />;
    }
    return <Status pluginId={pluginId} state={state} error={error} />;
  };
  Gate.displayName = `PluginPanelGate(${panelId})`;
  return Gate;
}

/**
 * A component slot gate renders nothing (a loading placeholder in a top bar or
 * composer strip would be noise) but activates the plugin on mount; once the
 * plugin registered the real component under the same id it is rendered.
 */
export function makeSlotGate(pluginId: string, slotId: string): ComponentType<SlotComponentProps> {
  const Gate = (props: SlotComponentProps) => {
    const { state } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onSlot:${slotId}`);
    }, []);
    if (state !== 'active') return null;
    for (const registry of [COMPOSER_EXTENSIONS, SIDEBAR_SECTIONS, AGENT_CARD_BADGES, TOP_BAR_ITEMS]) {
      const entry = registry.list().find((e) => e.id === slotId && e.owner === pluginId && e.value.component !== Gate);
      if (entry) {
        const Real = entry.value.component;
        return <Real {...props} />;
      }
    }
    return null;
  };
  Gate.displayName = `PluginSlotGate(${slotId})`;
  return Gate;
}

/**
 * A message-renderer gate matches on the declared shape (role / content
 * prefix), activates the plugin the first time such a message renders, and
 * hands over to the real renderer once registered; until then it renders
 * nothing so the shell's own bubble is not duplicated (the outlet falls back
 * to the shell when the gate yields null).
 */
export function makeRendererGate(pluginId: string, rendererId: string, shape: { role?: string; contentPrefix?: string }): MessageRendererDef {
  const Gate = (props: MessageRendererProps) => {
    const { state } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onRenderer:${rendererId}`);
    }, []);
    const entry = MESSAGE_RENDERERS.list().find((e) => e.id === rendererId && e.owner === pluginId && e.value.component !== Gate);
    if (state === 'active' && entry) {
      const Real = entry.value.component;
      return <Real {...props} />;
    }
    return null;
  };
  Gate.displayName = `PluginRendererGate(${rendererId})`;
  return {
    match: (message) => {
      const m = message as { role?: string; content?: string } | null;
      if (!m) return false;
      if (shape.role && m.role !== shape.role) return false;
      if (shape.contentPrefix && !(m.content ?? '').startsWith(shape.contentPrefix)) return false;
      return Boolean(shape.role || shape.contentPrefix);
    },
    component: Gate,
  };
}

/** A timeline-event gate for a declared event type: activates on first render, then defers to the real component. */
export function makeTimelineGate(pluginId: string, eventId: string, type: string): ComponentType<TimelineEventProps> {
  const Gate = (props: TimelineEventProps) => {
    const { state } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onTimelineEvent:${eventId}`);
    }, []);
    const entry = TIMELINE_EVENTS.list().find((e) => e.id === type && e.owner === pluginId && e.value.component !== Gate);
    if (state === 'active' && entry) {
      const Real = entry.value.component;
      return <Real {...props} />;
    }
    return null;
  };
  Gate.displayName = `PluginTimelineGate(${type})`;
  return Gate;
}

/**
 * An artifact-kind gate: the declarative descriptor (read-only, no preview) whose
 * renderer activates the plugin the first time an artifact of that kind is opened
 * and hands over to the real descriptor once the plugin registered it under the
 * same id. Until then the artifact view shows the plugin's status rather than
 * the shell's "unsupported kind" copy — the kind IS supported, just not loaded.
 */
export function makeArtifactKindGate(pluginId: string, kind: string, shape: { label?: string; downloadExt?: string }): KindDescriptor {
  const Gate = (props: { artifact: Artifact }) => {
    const { state, error } = useActivation(pluginId);
    useEffect(() => {
      void fireActivation(`onArtifactKind:${kind}`);
    }, []);
    const entry = ARTIFACT_KINDS.list().find((e) => e.id === kind && e.owner === pluginId && e.value.renderer !== Gate);
    if (state === 'active' && entry) {
      const Real = entry.value.renderer;
      return <Real {...props} />;
    }
    return <Status pluginId={pluginId} state={state} error={error} />;
  };
  Gate.displayName = `PluginArtifactKindGate(${kind})`;
  return {
    renderer: Gate,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'none',
    label: shape.label || undefined,
    downloadExt: shape.downloadExt || undefined,
  };
}
