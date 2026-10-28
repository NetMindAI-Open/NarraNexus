/**
 * @file_name: kindRegistry.ts
 * @author: NetMind.AI
 * @date: 2026-08-19
 * @description: The shell's builtin artifact kinds (registered into ARTIFACT_KINDS by platform/builtin.ts) and the download-extension helper.
 *
 * Before this file, kind knowledge was scattered: ArtifactRenderer held the
 * renderer map, ArtifactPreviewCard a chain of `kind ===` branches,
 * ArtifactDownloadMenu its own ext map + `isChart`, ArtifactsSection a label
 * map. Adding a kind (or a capability like editing) meant hunting all of
 * them. Now every consumer looks up one descriptor in the `ARTIFACT_KINDS`
 * registry (`platform/registries/artifactKinds.ts`, which also owns the
 * descriptor vocabulary), and a new builtin kind is one entry in
 * `BUILTIN_ARTIFACT_KINDS` + one renderer file; a plugin's kind is one
 * `host.registries.artifactKinds.register(...)`.
 *
 * `editSurface` / `saveMode` / `selectionToAI` encode the 2026-08-19 editing
 * design (no-mode framework): the tab shell mounts editing behaviour
 * declaratively from these fields. `office-watch` and `selectionToAI` are
 * declared here but consumed by later commits (office editing spec / the
 * selection→AI channel) — declaring them now means those commits don't have
 * to touch the shape of this table.
 */

import { lazy } from 'react';
import type { Artifact, BuiltinArtifactKind } from '@/types/artifact';
import { ARTIFACT_KINDS, type KindDescriptor } from '@/platform/registries';

export type { EditSurface, KindDescriptor, PreviewStrategy, RendererComponent, SaveMode } from '@/platform/registries';

const HtmlRenderer = lazy(() => import('./renderers/HtmlRenderer'));
const ChartRenderer = lazy(() => import('./renderers/ChartRenderer'));
const CsvRenderer = lazy(() => import('./renderers/CsvRenderer'));
const ImageRenderer = lazy(() => import('./renderers/ImageRenderer'));
const MarkdownRenderer = lazy(() => import('./renderers/MarkdownRenderer'));
const PdfRenderer = lazy(() => import('./renderers/PdfRenderer'));
const OfficeWatchViewer = lazy(() => import('./OfficeWatchViewer'));
const UrlRenderer = lazy(() => import('./renderers/UrlRenderer'));

/**
 * The shell's own kinds, exhaustive over `BuiltinArtifactKind`. Registered
 * into `ARTIFACT_KINDS` under owner `builtin.ui` by `platform/builtin.ts`
 * (the one place the shell contributes to its registries); consumers never
 * read this table directly — they look the registry up, so a plugin's kind
 * and a builtin one resolve the same way.
 */
export const BUILTIN_ARTIFACT_KINDS: Record<BuiltinArtifactKind, KindDescriptor> = {
  'text/html': {
    renderer: HtmlRenderer,
    editSurface: 'per-element',
    saveMode: 'element-commit',
    selectionToAI: true,
    preview: 'placeholder',
    previewPlaceholderKey: 'artifacts.preview.html',
    downloadExt: 'html',
    label: 'HTML',
  },
  'application/vnd.echarts+json': {
    renderer: ChartRenderer,
    // Canvas render: no selectable text, and the source-editor escape hatch
    // died with the mode toggle — chart changes are the AI's job.
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'placeholder',
    previewPlaceholderKey: 'artifacts.preview.chart',
    downloadExt: 'json',
    label: 'Chart',
    chartImageExport: true,
  },
  'text/csv': {
    renderer: CsvRenderer,
    editSurface: 'resident-editor',
    saveMode: 'explicit-dirty',
    selectionToAI: true,
    preview: 'csv-head',
    downloadExt: 'csv',
    label: 'CSV',
  },
  'text/markdown': {
    renderer: MarkdownRenderer,
    editSurface: 'block-editor',
    saveMode: 'debounced-autosave',
    selectionToAI: true,
    preview: 'md-head',
    downloadExt: 'md',
    label: 'Markdown',
  },
  'image/png': {
    renderer: ImageRenderer,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'image',
    downloadExt: 'png',
    label: 'PNG',
  },
  'image/jpeg': {
    renderer: ImageRenderer,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'image',
    downloadExt: 'jpg',
    label: 'JPEG',
  },
  'application/pdf': {
    renderer: PdfRenderer,
    // PDF is always a downstream product here — editing the projection would
    // fork it from its source. PDF.js text selection still works as a quote
    // anchor for the AI channel.
    editSurface: 'none',
    saveMode: null,
    selectionToAI: true,
    preview: 'placeholder',
    previewPlaceholderKey: 'artifacts.preview.pdf',
    downloadExt: 'pdf',
    label: 'PDF',
  },
  'application/vnd.officecli-live': {
    renderer: OfficeWatchViewer,
    editSurface: 'office-watch',
    saveMode: 'office-resident',
    selectionToAI: true,
    preview: 'none',
  },
  'application/x-url': {
    renderer: UrlRenderer,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'none',
  },
};

/**
 * The download extension for one artifact: the registry's static extension
 * when the kind has one, else the file_path's own extension (sanitized —
 * it feeds a filename), else 'bin'. This is the fix for the Shenzhen-r2
 * ".bin download" bug: an office artifact's real extension (pptx/docx/xlsx)
 * lives only in its pointer, and the kind is an internal render marker,
 * not a transport type.
 */
export function downloadExtFor(artifact: Pick<Artifact, 'kind' | 'file_path'>): string {
  // `kind` is a server value: a NEWER backend may ship a kind this build
  // does not know and no plugin has registered.
  const staticExt = ARTIFACT_KINDS.get(artifact.kind)?.downloadExt;
  if (staticExt) return staticExt;
  const base = (artifact.file_path ?? '').split('/').pop() ?? '';
  const dot = base.lastIndexOf('.');
  const ext = dot > 0 ? base.slice(dot + 1) : '';
  return /^[A-Za-z0-9]{1,16}$/.test(ext) ? ext.toLowerCase() : 'bin';
}
