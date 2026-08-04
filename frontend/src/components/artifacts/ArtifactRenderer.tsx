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
import { useTranslation } from 'react-i18next';
import type { Artifact, ArtifactKind } from '@/types/artifact';

const HtmlRenderer = lazy(() => import('./renderers/HtmlRenderer'));
const ChartRenderer = lazy(() => import('./renderers/ChartRenderer'));
const CsvRenderer = lazy(() => import('./renderers/CsvRenderer'));
const ImageRenderer = lazy(() => import('./renderers/ImageRenderer'));
const MarkdownRenderer = lazy(() => import('./renderers/MarkdownRenderer'));
const PdfRenderer = lazy(() => import('./renderers/PdfRenderer'));
const OfficeWatchViewer = lazy(() => import('./OfficeWatchViewer'));
const UrlRenderer = lazy(() => import('./renderers/UrlRenderer'));

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
  // Office docs render LIVE (officecli watch) instead of a static file — the
  // viewer opens a watch on the artifact's file and streams SSE refreshes.
  'application/vnd.officecli-live': OfficeWatchViewer,
  // URL tabs: the entry doc holds a URL + embed verdict; the renderer iframes
  // the page or shows a fallback card.
  'application/x-url': UrlRenderer,
};

interface Props {
  artifact: Artifact;
  /**
   * true (default, the column): wrap the renderer in an h-full/w-full
   * overflow-auto box that owns ALL scrolling — the column's content box is
   * overflow-hidden by design (drag-freeze relies on clipping) and renderer
   * roots are auto-height, so without this bound nothing ever overflows a
   * scrollable container and content past the first screen is clipped.
   *
   * false (the zoom modal): no wrapper. The modal's scale-sizer layers have
   * fixed heights and its own outer overflow-auto container must carry the
   * scrolling — renderers overflow it naturally, and the zoom mechanism
   * depends on that overflow. A bounded box here would clamp content to one
   * screen and nest a second scrollbar inside the scaled layer.
   */
  bounded?: boolean;
}

export default function ArtifactRenderer({ artifact, bounded = true }: Props) {
  const { t } = useTranslation();
  const Renderer = RENDERER_BY_KIND[artifact.kind];
  if (!Renderer) {
    return <div className="p-4 opacity-60">{t('artifacts.unsupportedKind', { kind: artifact.kind })}</div>;
  }
  const body = (
    <Suspense fallback={<div className="p-4 opacity-60">{t('artifacts.loadingRenderer')}</div>}>
      <Renderer artifact={artifact} />
    </Suspense>
  );
  if (!bounded) return body;
  // h-full/w-full, NOT absolute inset-0 — absolute would need a positioned
  // ancestor and this component's placement is the caller's concern.
  return <div className="h-full w-full overflow-auto overscroll-contain">{body}</div>;
}
