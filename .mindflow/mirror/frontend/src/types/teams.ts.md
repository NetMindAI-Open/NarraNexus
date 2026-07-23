---
code_file: frontend/src/types/teams.ts
last_verified: 2026-07-20
stub: false
---

## 2026-07-20 — TeamChatMessage.attachments

`TeamChatMessage` gained `attachments?: BusAttachment[]` so team-chat bubbles can
render files sent/shared into the room (see [[BusAttachmentList]]).

## 2026-07-13 — skill-secret bundle types

`BundleExportRequest.include_skill_secrets` and `BundleManifest.contains_skill_secrets`.

## 2026-07-13 — bundle credential types

`BundleExportRequest.include_channel_credentials`, `BundlePreflightResponse.credential_clashes`, `BundleManifest.contains_channel_credentials`, and confirm counters `channel_credentials_imported` / `channel_credentials_skipped_conflict`.

# teams.ts — Frontend types for teams (incl. team group chat) + bundle export/import

## Why it exists

Mirrors the backend's Team / TeamMember / TeamChat / Bundle request/response
shapes into TypeScript interfaces so the frontend stays field-for-field aligned
with the Pydantic models. When adding a type, change the backend Pydantic first,
then mirror it here — otherwise runtime field-name drift bites silently.

## How it works / design

- **Team group chat is the new core.** A team is now a group chat over the
  message bus, so the file carries the chat wire types: `TeamChatMessage`
  (`from_agent` is `usr_<user_id>` for the human, else an agent_id; `is_user`
  disambiguates rendering), `TeamChatHistoryResponse` (messages + a `thinking[]`
  of member agent_ids the trigger is mid-processing → the "…" indicator), and
  `TeamChatSendResponse`. These back `api.getTeamChat` / `api.sendTeamChat`
  ([[api]]). `@mention` delivery is expressed on the send side, not in these
  shapes — see `sendTeamChat`'s `mentions` arg.
- **Team CRUD types**: `Team`, `TeamWithMembers`, `TeamListResponse`,
  `TeamOperationResponse` back [[TeamManagementModal]] / [[teamsStore]].
  `intro_md` doubles as the bundle's default README.
- **Bundle export/import types** (subproject 2) live here too: `BundleExportRequest`
  with its many per-agent opt-in allowlists (`mcp_selection` opt-in by design,
  `narrative_/event_/job_/artifact_selection` null = include all),
  `BundleManifest` / `BundlePreflightResponse` / `BundleConfirmResponse`, plus the
  wizard preview types and `SkillArchiveRecord`.
- **Gotcha**: same skill name can map to N agents (`SkillExportSpec.agent_id` +
  `skill_dir` disambiguate the physical folder); `skill_name` from frontmatter is
  NOT filesystem-unique.

## 2026-07-21 — Team.lead_agent_id

`Team` gained `lead_agent_id?` (default responder; null = earliest member). Set via the
TeamManagementModal picker → `updateTeam`. See backend [[teams]].
