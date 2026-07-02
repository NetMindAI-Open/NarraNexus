/**
 * @file_name: you.ts
 * @date: 2026-06-23
 * @description: Types for the owner-scoped "You" workspace (/api/me/*).
 * Cross-agent aggregates, distinct from the per-agent /api/agents types.
 */

/** One storyline on the Narra Memory timeline — a narrative belonging to one
 *  of the user's agents (see backend/routes/me.py `get_my_narratives`). */
export interface MyNarrative {
  narrative_id: string;
  agent_id: string;
  agent_name: string;
  type: string;
  /** 'default' = seeded scaffold bucket; anything else = a lived storyline. */
  is_special: string;
  /** narrative_info.name */
  name: string;
  /** narrative_info.current_summary || description */
  summary: string;
  topic_hint: string;
  topic_keywords: string[];
  round_counter: number;
  /** ISO 8601 (UTC) */
  created_at: string | null;
  updated_at: string | null;
}

export interface MyNarrativesResponse {
  success: boolean;
  narratives: MyNarrative[];
  error?: string;
}

/** One node in the owner-level Nexus Network — an entity the user's agents
 *  know, merged across every agent that knows it (see /api/me/network). */
export interface MyNetworkEntity {
  key: string;
  name: string;
  /** user | agent | group */
  type: string;
  /** true when this entity is the owner themselves (the graph centre). */
  is_self: boolean;
  /** direct | known_of */
  familiarity: string;
  /** 0..1 (best across agents) */
  strength: number;
  /** summed across agents */
  interactions: number;
  last_interaction_time: string | null;
  description: string;
  expertise_domains: string[];
  /** display names of the user's agents that know this entity. */
  known_by: string[];
}

export interface MyNetworkResponse {
  success: boolean;
  entities: MyNetworkEntity[];
  error?: string;
}

/** One lens in the Worldview — how a single agent sees the user, plus a
 *  glimpse of that agent's own worldview (see /api/me/worldview). */
export interface MyWorldviewLens {
  agent_id: string;
  agent_name: string;
  /** the agent's persona / characterization of you */
  sees_you: string;
  /** a few of the agent's own world observations */
  worldview: string[];
}

export interface MyWorldviewResponse {
  success: boolean;
  lenses: MyWorldviewLens[];
  error?: string;
}
