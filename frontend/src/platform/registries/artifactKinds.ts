/**
 * @file_name: artifactKinds.ts
 * @author: Bin Liang
 * @date: 2026-09-07
 * @description: Artifact-kind registry — what each ArtifactKind can do (render / edit / save / preview / download), as a registry.
 *
 * The descriptor vocabulary (`EditSurface` / `SaveMode` / `PreviewStrategy`)
 * encodes the 2026-08-19 no-mode editing design; it moved here verbatim
 * from `components/artifacts/kindRegistry.ts`, which now holds only the
 * shell's builtin table (registered by `platform/builtin.ts`) and the
 * download-extension helper. Being a `Registry` puts artifact kinds on the
 * same footing as every other UI extension point: owner-scoped (a plugin
 * cannot silently override the shell's `text/html`), disposed with the
 * plugin, subscribable (a renderer registered after first paint re-renders
 * the open artifact), and on `HostAPI.registries` without a second API.
 *
 * Registration validates the descriptor's internal invariants (a save mode
 * exists exactly for editable surfaces; a placeholder preview names its
 * i18n line), so a malformed plugin kind fails at activation, not at render.
 */
import type { ComponentType, LazyExoticComponent } from 'react';
import type { Artifact } from '@/types/artifact';

import { Registry } from './registry';

/** A kind's renderer: lazy (the shell's, one chunk each) or a plain component (a plugin's). */
export type RendererComponent =
  | LazyExoticComponent<ComponentType<{ artifact: Artifact }>>
  | ComponentType<{ artifact: Artifact }>;

/**
 * Which editing surface the tab offers. There is deliberately NO view/edit
 * mode toggle anywhere — the render surface itself takes a cursor where the
 * kind allows it (no-mode framework):
 *  - block-editor:    the render IS a WYSIWYG editor (markdown).
 *  - per-element:     rendered page, but clicking into static text makes that
 *                     one element editable; blur commits (html).
 *  - resident-editor: the source editor IS the render, always editable (csv).
 *  - office-watch:    live officecli watch page; edits go through officecli
 *                     command translation (office editing spec).
 *  - none:            read-only surface; user changes go through the AI.
 */
export type EditSurface =
  | 'block-editor'
  | 'per-element'
  | 'resident-editor'
  | 'office-watch'
  | 'none';

/**
 * How user edits reach disk. Null exactly when editSurface is 'none'.
 *  - debounced-autosave: continuous typing, saved on blur + idle (markdown).
 *  - element-commit:     each element blur is one atomic commit (html).
 *  - explicit-dirty:     Cmd+S / save button with dirty guards (csv).
 *  - office-resident:    officecli resident serializes all writers.
 */
export type SaveMode =
  | 'debounced-autosave'
  | 'element-commit'
  | 'explicit-dirty'
  | 'office-resident'
  | null;

/** What ArtifactPreviewCard shows inside a chat message. */
export type PreviewStrategy = 'image' | 'csv-head' | 'md-head' | 'placeholder' | 'none';

export interface KindDescriptor {
  renderer: RendererComponent;
  editSurface: EditSurface;
  saveMode: SaveMode;
  /** v1.5 mount point: can a selection on this surface be sent to the AI? */
  selectionToAI: boolean;
  preview: PreviewStrategy;
  /** i18n key for the preview line; present iff preview === 'placeholder'. */
  previewPlaceholderKey?: string;
  /**
   * Download filename extension. Absent when the kind has no SINGLE natural
   * extension (office-live covers pptx/docx/xlsx) — consumers must go
   * through downloadExtFor, which derives it from the artifact's file_path.
   * A static 'bin' here was the Shenzhen-r2 ".bin download" bug.
   */
  downloadExt?: string;
  /** Human label for admin lists; consumers fall back to the raw kind. */
  label?: string;
  /** Chart-only: PNG/JPEG export entries in the download menu. */
  chartImageExport?: boolean;
}


export function validateKindDescriptor(d: KindDescriptor): void {
  if (!d.renderer) throw new Error('artifact kind: renderer is required');
  if ((d.editSurface === 'none') !== (d.saveMode === null)) {
    throw new Error(`artifact kind: saveMode must be null exactly when editSurface is 'none' (got ${d.editSurface} / ${String(d.saveMode)})`);
  }
  if ((d.preview === 'placeholder') !== Boolean(d.previewPlaceholderKey)) {
    throw new Error("artifact kind: previewPlaceholderKey is required exactly when preview is 'placeholder'");
  }
}

export const ARTIFACT_KINDS = new Registry<KindDescriptor>('ui.artifactKinds', { validate: validateKindDescriptor });
