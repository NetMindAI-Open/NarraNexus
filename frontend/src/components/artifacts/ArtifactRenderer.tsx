/**
 * @file_name: ArtifactRenderer.tsx
 * @description: Shared kind → renderer dispatch for artifact content.
 *
 * Pulled out of ArtifactColumn so the embedded column view AND the
 * zoom modal can render artifacts through the same lazy-loaded renderer
 * chunks. Two renderer instances for the same kind do NOT trigger duplicate
 * chunk downloads — React.lazy memoises by import.
 *
 * Pointer model: renderers no longer take a `version` prop; they mint a view
 * token via `useArtifactRawUrl` and load content from the public raw route.
 */

import { lazy, Suspense } from 'react';
import type { Artifact, ArtifactKind } from '@/types/artifact';

const HtmlRenderer = lazy(() => import('./renderers/HtmlRenderer'));
const ChartRenderer = lazy(() => import('./renderers/ChartRenderer'));
const CsvRenderer = lazy(() => import('./renderers/CsvRenderer'));
const ImageRenderer = lazy(() => import('./renderers/ImageRenderer'));
const MarkdownRenderer = lazy(() => import('./renderers/MarkdownRenderer'));
const PdfRenderer = lazy(() => import('./renderers/PdfRenderer'));

type RendererComponent = React.LazyExoticComponent<
  React.ComponentType<{ artifact: Artifact }>
>;

const RENDERER_BY_KIND: Record<ArtifactKind, RendererComponent> = {
  'text/html': HtmlRenderer,
  'application/vnd.echarts+json': ChartRenderer,
  'text/csv': CsvRenderer,
  'text/markdown': MarkdownRenderer,
  'image/png': ImageRenderer,
  'image/jpeg': ImageRenderer,
  // PDF: dedicated PdfRenderer uses <object> instead of the sandboxed iframe
  // to avoid breaking Firefox PDF.js (needs same-origin XHR) and WKWebView.
  'application/pdf': PdfRenderer,
};

interface Props {
  artifact: Artifact;
}

export default function ArtifactRenderer({ artifact }: Props) {
  const Renderer = RENDERER_BY_KIND[artifact.kind];
  if (!Renderer) {
    return <div className="p-4 opacity-60">Unsupported artifact kind: {artifact.kind}</div>;
  }
  return (
    <Suspense fallback={<div className="p-4 opacity-60">Loading renderer…</div>}>
      <Renderer artifact={artifact} />
    </Suspense>
  );
}
