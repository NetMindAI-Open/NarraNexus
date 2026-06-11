/**
 * @file_name: HelpOverlay.tsx
 * @author:
 * @date: 2026-06-11
 * @description: Hand-annotated, MULTI-PAGE help overlay — "another hand
 * wrote on the paper". Dims the page; handwritten notes sit in left /
 * right rails (stacked, never overlapping) with wobbly arrows to the
 * live controls. Top-center: the current guide's big handwritten title
 * + "guide N of M". Bottom-center: a "got it" close control with the
 * NUMBERED page pills beneath it — active pill is solid so switching
 * intent reads at a glance (Owner round-3 feedback).
 *
 * Type scale: Caveat renders visually small for its point size, so all
 * sizes are compensated upward (note 26 / detail 19 / title 34).
 *
 * Anchor registry: controls carry data-help-id; measurement happens at
 * open / page-switch / resize and silently skips missing or invisible
 * anchors. Annotation ink is theme-STABLE light (--color-gray-50) —
 * never --nm-* tokens, which flip in dark mode (2026-06-11 lesson).
 */

import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import type { HelpPage } from './helpContent';
import {
  measureAnnotations,
  layoutAnnotations,
  type PlacedAnnotation,
} from './measure';
import { wobblyArrow, wobblyLeader, wobblyLine } from './wobble';

const INK = 'var(--color-gray-50)';

interface HelpOverlayProps {
  open: boolean;
  pages: HelpPage[];
  onClose: () => void;
}

export function HelpOverlay({ open, pages, onClose }: HelpOverlayProps) {
  const [pageIdx, setPageIdx] = useState(0);
  const [resizeTick, setResizeTick] = useState(0);

  const page = pages[Math.min(pageIdx, pages.length - 1)];

  // Pure render-time derivation (DOM reads only); re-runs on page
  // switch and window resize.
  const placed = useMemo<PlacedAnnotation[]>(() => {
    if (!open || !page) return [];
    return layoutAnnotations(
      measureAnnotations(page.annotations),
      window.innerWidth,
      window.innerHeight,
    );
    // resizeTick: same inputs, but the DOM geometry it reads has changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, page, resizeTick]);

  useEffect(() => {
    if (!open) return;
    const onResize = () => setResizeTick((t) => t + 1);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('resize', onResize);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('resize', onResize);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  // Fresh open always starts on the first page.
  useEffect(() => {
    if (open) setPageIdx(0);
  }, [open]);

  if (!open || !page) return null;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Page guide"
      className="fixed inset-0 z-[300] animate-fade-in"
      style={{ background: 'var(--nm-backdrop, rgba(20,16,12,0.5))' }}
      onClick={onClose}
    >
      {/* Stroke layer */}
      <svg className="absolute inset-0 w-full h-full pointer-events-none" aria-hidden>
        {placed.map((m, i) => (
          <g
            key={`${page.id}:${m.helpId}`}
            className="help-stroke-in"
            style={{ animationDelay: `${i * 60}ms` }}
          >
            {m.kind === 'region' && m.underline && (
              <path
                d={wobblyLine(
                  { x: m.underline.x - m.underline.width / 2, y: m.underline.y },
                  { x: m.underline.x + m.underline.width / 2, y: m.underline.y },
                  2.5,
                )}
                fill="none"
                stroke={INK}
                strokeWidth={1.6}
                strokeLinecap="round"
                opacity={0.8}
              />
            )}
            {m.kind === 'point' && (
            <path
              d={
                m.laneX !== undefined
                  ? wobblyLeader(m.from, m.to, m.laneX)
                  : wobblyArrow(m.from, m.to)
              }
              fill="none"
              stroke={INK}
              strokeWidth={1.8}
              strokeLinecap="round"
            />
            )}
          </g>
        ))}
      </svg>

      {/* Top-center: current guide title — anchors the page concept. */}
      <div
        className="absolute top-5 left-1/2 -translate-x-1/2 text-center pointer-events-none help-note-in"
        style={{ fontFamily: 'var(--font-handwriting)', color: INK }}
      >
        <div style={{ fontSize: 34, lineHeight: 1.1 }}>{page.label}</div>
        <div style={{ fontSize: 16, opacity: 0.7, marginTop: 2 }}>
          guide {pageIdx + 1} of {pages.length}
        </div>
      </div>

      {/* Note layer — real DOM text (screen-reader readable). */}
      {placed.map((m, i) => (
        <div
          key={`${page.id}:${m.helpId}`}
          className="absolute help-note-in"
          style={{
            left: m.noteX,
            top: m.noteY,
            width: m.noteW,
            textAlign: m.align,
            animationDelay: `${i * 60}ms`,
            fontFamily: 'var(--font-handwriting)',
            color: INK,
            textShadow: '0 1px 2px rgba(0,0,0,0.35)',
          }}
        >
          <div style={{ fontSize: 26, lineHeight: 1.15 }}>{m.note}</div>
          {m.detail && (
            <div style={{ fontSize: 19, lineHeight: 1.28, opacity: 0.85, marginTop: 4 }}>
              {m.detail}
            </div>
          )}
        </div>
      ))}

      {placed.length === 0 && (
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{ fontFamily: 'var(--font-handwriting)', fontSize: 24, color: INK }}
        >
          Nothing to explain on this page yet.
        </div>
      )}

      {/* Bottom-center controls: got it (close) + page tabs beneath. */}
      <div
        className="absolute left-1/2 -translate-x-1/2 bottom-6 flex flex-col items-center gap-3"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          aria-label="Close guide"
          onClick={onClose}
          className="px-6 py-1.5 rounded-full cursor-pointer transition-transform hover:scale-105"
          style={{
            fontFamily: 'var(--font-handwriting)',
            fontSize: 25,
            lineHeight: 1.2,
            color: INK,
            border: `1.5px solid ${INK}`,
          }}
        >
          got it
        </button>

        <div
          style={{
            fontFamily: 'var(--font-handwriting)',
            fontSize: 15,
            color: INK,
            opacity: 0.7,
          }}
          aria-hidden
        >
          more guides — click to switch
        </div>

        <div role="tablist" aria-label="Guide pages" className="flex items-center gap-2.5">
          {pages.map((p, i) => {
            const activePage = i === pageIdx;
            return (
              <button
                key={p.id}
                role="tab"
                aria-selected={activePage}
                aria-label={p.label}
                onClick={() => setPageIdx(i)}
                className="px-4 py-1 rounded-full cursor-pointer transition-all hover:scale-105"
                style={{
                  fontFamily: 'var(--font-handwriting)',
                  fontSize: 19,
                  lineHeight: 1.25,
                  // Active pill is SOLID light ink with dark text — the
                  // selected/unselected contrast that makes "these are
                  // switchable pages" legible at a glance.
                  color: activePage ? 'var(--color-gray-900)' : INK,
                  background: activePage ? INK : 'transparent',
                  border: `1.2px solid ${activePage ? INK : 'rgba(250,250,247,0.45)'}`,
                }}
              >
                {i + 1} · {p.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}
