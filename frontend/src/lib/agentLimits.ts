/**
 * @file_name: agentLimits.ts
 * @description: Shared agent field length ceiling for the UI.
 *
 * Mirror of AGENT_TEXT_MAX_LENGTH in
 * src/xyz_agent_context/schema/entity_schema.py. agent_name / agent_description
 * are capped at this length at the write edge (server returns 422 past it) and
 * trimmed to it on bundle import; the UI enforces the same limit so the user
 * sees the ceiling before submitting rather than after a server error.
 */
export const AGENT_TEXT_MAX_LENGTH = 255;
