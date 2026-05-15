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

export type ArtifactKind =
  | 'text/html'
  | 'application/vnd.echarts+json'
  | 'text/csv'
  | 'text/markdown'
  | 'image/png'
  | 'image/jpeg'
  | 'application/pdf';

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
  /** Entry file, relative to settings.base_working_path. */
  file_path: string;
  /** Recursive size of the artifact root directory. */
  size_bytes: number;
  created_at: string;
  updated_at: string;
}
