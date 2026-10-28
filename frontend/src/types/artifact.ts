/**
 * Artifact type definitions — mirrors backend Artifact (pointer model, 2026-05-14).
 *
 * An artifact is a pointer to an entry file the agent wrote in its workspace.
 * `file_path` is the entry relative to base_working_path; the entry's
 * directory is the artifact root (served wholesale so multi-file HTML apps
 * can reference sibling assets).
 *
 * To load raw content, fetch a short-TTL view token first:
 *
 *     const dirUrl = await artifactsApi.getRawUrl(agentId, artifactId);
 *     // dirUrl is something like "/api/public/artifacts/raw/{token}/"
 *     // — point an iframe at it for HTML, or fetch from it for others.
 *
 * See `hooks/useArtifactRawUrl.ts` for the convenience hook.
 */

/** Artifact kinds the shell ships renderers for. */
export type BuiltinArtifactKind =
  | 'text/html'
  | 'application/vnd.echarts+json'
  | 'text/csv'
  | 'text/markdown'
  | 'image/png'
  | 'image/jpeg'
  | 'application/pdf'
  // Office document (.pptx/.docx/.xlsx) — rendered as a LIVE officecli-watch
  // preview (auto-refreshes as the agent edits), not a static file.
  | 'application/vnd.officecli-live'
  // A web page opened as a tab. The entry file is a small JSON doc
  // (UrlArtifactDoc); the renderer iframes the URL or falls back per the
  // embed verdict.
  | 'application/x-url';

// A plugin may register a renderer for a new kind (the `ARTIFACT_KINDS`
// registry, `platform/registries/artifactKinds.ts`), and a newer backend may
// ship a kind this build does not know, so the wire type is open.
export type ArtifactKind = BuiltinArtifactKind | (string & {});

/** How a URL tab should be surfaced. Mirrors backend EmbedMode. */
export type EmbedMode = 'iframe' | 'stream';

/** The embed decision for a URL tab. Mirrors backend EmbedVerdict. */
export interface EmbedVerdict {
  recommended: EmbedMode;
  reason: string;
  probe_status: 'ok' | 'failed' | 'skipped';
  user_override: EmbedMode | null;
}

/** The on-disk entry doc of a URL tab (page.url.json). Mirrors UrlArtifactDoc. */
export interface UrlArtifactDoc {
  schema_version: number;
  url: string;
  title: string;
  embed: EmbedVerdict | null;
}

/** Collapse recommended + override into the mode the renderer should use. */
export function effectiveEmbedMode(embed: EmbedVerdict | null | undefined): EmbedMode {
  if (!embed) return 'iframe';
  return embed.user_override ?? embed.recommended;
}

export interface Artifact {
  artifact_id: string;
  agent_id: string;
  user_id: string;
  session_id: string | null;
  original_session_id?: string | null;
  title: string;
  kind: ArtifactKind;
  description: string | null;
  pinned: boolean;
  /**
   * Owning team, or null for a private artifact. Null is also every artifact
   * created before team workspaces existed, so "no team" and "private" are
   * the same state — no migration flag to check anywhere.
   */
  team_id: string | null;
  /** Entry file, relative to settings.base_working_path. */
  file_path: string;
  /** Recursive size of the artifact root directory. */
  size_bytes: number;
  /**
   * sha256 of the entry file at the last commit point (register / re-register
   * / user edit). Editors do NOT use this as their lock base — they hash the
   * bytes they actually loaded (disk may have moved on) — but the PUT
   * /content response returns it so a successful save can rebase.
   */
  content_hash?: string | null;
  created_at: string;
  updated_at: string;
}


/**
 * One file shared into a team's folder (GET /api/teams/{id}/files).
 *
 * Mirrors a chosen wire shape, not the table: `id`, `owner_user_id` and
 * `content_hash` stay server-side.
 */
export interface TeamFile {
  file_id: string;
  /** Human-facing name. The on-disk name is the file_id, so this is the only
   *  place the original survives. */
  original_name: string;
  rel_path: string;
  size_bytes: number;
  mime_type: string | null;
  category: string | null;
  /** Which agent shared it — the team panel attributes every row. */
  shared_by_agent_id: string;
  /** Offset-aware ISO. Without the offset `Date.parse` reads it as local time
   *  and a file shared moments ago renders hours old. */
  created_at: string;
}
