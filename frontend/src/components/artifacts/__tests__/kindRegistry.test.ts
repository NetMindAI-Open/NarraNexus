/**
 * @file_name: kindRegistry.test.ts
 * @description: The kind capability registry is the single source of truth
 * for what each ArtifactKind can do (render / edit / save / preview /
 * download). These tests pin the v1 capability matrix decided in the
 * 2026-08-19 editing spec — a wrong entry here silently enables editing on a
 * kind that cannot round-trip, or kills it on one that can.
 */

import { describe, expect, it } from 'vitest';
import type { BuiltinArtifactKind } from '@/types/artifact';
import { ARTIFACT_KINDS, RegistryConflictError, type KindDescriptor } from '@/platform/registries';
import { BUILTIN_ARTIFACT_KINDS, downloadExtFor } from '../kindRegistry';

// The registry as populated by platform/builtin.ts (test-setup imports it);
// the capability matrix below is pinned on the builtin table itself.
const KIND_REGISTRY = BUILTIN_ARTIFACT_KINDS;

// Builtin-typed on purpose: a typo here must be a compile error, not a silent "unsupported kind".
const ALL_KINDS: BuiltinArtifactKind[] = [
  'text/html',
  'application/vnd.echarts+json',
  'text/csv',
  'text/markdown',
  'image/png',
  'image/jpeg',
  'application/pdf',
  'application/vnd.officecli-live',
  'application/x-url',
];

describe('BUILTIN_ARTIFACT_KINDS', () => {
  it('covers every builtin ArtifactKind and nothing else, and is what the registry holds', () => {
    expect(Object.keys(KIND_REGISTRY).sort()).toEqual([...ALL_KINDS].sort());
    for (const kind of ALL_KINDS) {
      expect(ARTIFACT_KINDS.get(kind), kind).toBe(KIND_REGISTRY[kind]);
      expect(ARTIFACT_KINDS.ownerOf(kind), kind).toBe('builtin.ui');
    }
  });

  it('every kind has a renderer', () => {
    for (const kind of ALL_KINDS) {
      expect(KIND_REGISTRY[kind].renderer, kind).toBeDefined();
    }
  });

  it('pins the edit-surface matrix (no-mode framework, 2026-08-19)', () => {
    const surfaces = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].editSurface]),
    );
    expect(surfaces).toEqual({
      'text/markdown': 'block-editor',
      'text/html': 'per-element',
      'text/csv': 'resident-editor',
      'application/vnd.echarts+json': 'none', // canvas: AI channel only
      'image/png': 'none',
      'image/jpeg': 'none',
      'application/pdf': 'none',
      'application/vnd.officecli-live': 'office-watch',
      'application/x-url': 'none',
    });
  });

  it('save mode exists exactly for editable surfaces', () => {
    expect(KIND_REGISTRY['text/markdown'].saveMode).toBe('debounced-autosave');
    expect(KIND_REGISTRY['text/html'].saveMode).toBe('element-commit');
    expect(KIND_REGISTRY['text/csv'].saveMode).toBe('explicit-dirty');
    expect(KIND_REGISTRY['application/vnd.officecli-live'].saveMode).toBe('office-resident');
    for (const kind of ALL_KINDS) {
      const d = KIND_REGISTRY[kind];
      if (d.editSurface === 'none') expect(d.saveMode, kind).toBeNull();
      else expect(d.saveMode, kind).not.toBeNull();
    }
  });

  it('keeps the download extensions the menu shipped with', () => {
    const ext = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].downloadExt]),
    );
    expect(ext).toEqual({
      'text/html': 'html',
      'application/vnd.echarts+json': 'json',
      'text/csv': 'csv',
      'text/markdown': 'md',
      'image/png': 'png',
      'image/jpeg': 'jpg',
      'application/pdf': 'pdf',
      // No static extension — the real one lives in file_path (a pptx and
      // a docx share this kind), so these derive per-artifact via
      // downloadExtFor. A static 'bin' here is exactly the Shenzhen-r2
      // ".bin download" bug.
      'application/vnd.officecli-live': undefined,
      'application/x-url': undefined,
    });
  });

  describe('downloadExtFor', () => {
    it('derives the office extension from file_path (the .bin bug)', () => {
      expect(
        downloadExtFor({
          kind: 'application/vnd.officecli-live',
          file_path: 'agent_a_user_b/ev-sales-trend.pptx',
        }),
      ).toBe('pptx');
      expect(
        downloadExtFor({
          kind: 'application/vnd.officecli-live',
          file_path: 'agent_a_user_b/ev-sales-trend-report.docx',
        }),
      ).toBe('docx');
      expect(
        downloadExtFor({
          kind: 'application/vnd.officecli-live',
          file_path: 'agent_a_user_b/budget.xlsx',
        }),
      ).toBe('xlsx');
    });

    it('a static registry extension wins over the file_path one', () => {
      // kind describes the ENTRY semantics; a markdown artifact whose
      // pointer ends .markdown still downloads as .md.
      expect(
        downloadExtFor({ kind: 'text/markdown', file_path: 'x/notes.markdown' }),
      ).toBe('md');
    });

    it('falls back to bin when neither side has an extension', () => {
      expect(
        downloadExtFor({ kind: 'application/vnd.officecli-live', file_path: 'x/deck' }),
      ).toBe('bin');
      expect(
        downloadExtFor({ kind: 'application/vnd.officecli-live', file_path: '' }),
      ).toBe('bin');
      expect(
        downloadExtFor({ kind: 'application/vnd.officecli-live', file_path: 'x/tar.' }),
      ).toBe('bin');
    });

    it('sanitizes a hostile file_path extension instead of trusting it', () => {
      // extension feeds a filename — length-capped and alnum-only
      expect(
        downloadExtFor({
          kind: 'application/vnd.officecli-live',
          file_path: 'x/a.' + 'q'.repeat(40),
        }),
      ).toBe('bin');
      expect(
        downloadExtFor({ kind: 'application/vnd.officecli-live', file_path: 'x/a.p tx' }),
      ).toBe('bin');
    });
  });

  it('chart image export is a chart-only affordance', () => {
    for (const kind of ALL_KINDS) {
      expect(Boolean(KIND_REGISTRY[kind].chartImageExport), kind).toBe(
        kind === 'application/vnd.echarts+json',
      );
    }
  });

  it('preview strategies match what ArtifactPreviewCard shipped with', () => {
    const previews = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].preview]),
    );
    expect(previews).toEqual({
      'image/png': 'image',
      'image/jpeg': 'image',
      'text/csv': 'csv-head',
      'text/markdown': 'md-head',
      'text/html': 'placeholder',
      'application/vnd.echarts+json': 'placeholder',
      'application/pdf': 'placeholder',
      'application/vnd.officecli-live': 'none',
      'application/x-url': 'none',
    });
    // placeholder kinds must say which i18n line to show
    for (const kind of ALL_KINDS) {
      const d = KIND_REGISTRY[kind];
      if (d.preview === 'placeholder') {
        expect(d.previewPlaceholderKey, kind).toBeTruthy();
      } else {
        expect(d.previewPlaceholderKey, kind).toBeUndefined();
      }
    }
  });

  it('labels cover exactly the kinds the settings list used to label', () => {
    expect(KIND_REGISTRY['text/html'].label).toBe('HTML');
    expect(KIND_REGISTRY['application/vnd.echarts+json'].label).toBe('Chart');
    expect(KIND_REGISTRY['text/csv'].label).toBe('CSV');
    expect(KIND_REGISTRY['text/markdown'].label).toBe('Markdown');
    expect(KIND_REGISTRY['image/png'].label).toBe('PNG');
    expect(KIND_REGISTRY['image/jpeg'].label).toBe('JPEG');
    expect(KIND_REGISTRY['application/pdf'].label).toBe('PDF');
    // pre-registry fallback for these was the raw kind string — keep it
    expect(KIND_REGISTRY['application/vnd.officecli-live'].label).toBeUndefined();
    expect(KIND_REGISTRY['application/x-url'].label).toBeUndefined();
  });

  it('declares selection→AI mounts per the editability matrix (v1.5 hook)', () => {
    const sel = Object.fromEntries(
      ALL_KINDS.map((k) => [k, KIND_REGISTRY[k].selectionToAI]),
    );
    expect(sel).toEqual({
      'text/markdown': true,
      'text/html': true,
      'text/csv': true,
      'application/pdf': true, // PDF.js selection as quote anchor
      'application/vnd.officecli-live': true, // watch `selected` → agent
      'application/vnd.echarts+json': false, // canvas has no selectable text
      'image/png': false,
      'image/jpeg': false,
      'application/x-url': false,
    });
  });
  it('pins the PUT-editable kind set to the backend (review #334 I18)', () => {
    // Backend twin: tests/artifact/test_user_edit.py
    // ::test_editable_kinds_pinned_to_frontend_registry — both sides pin the
    // SAME literal set; a kind added on one side without the other shows as
    // an editor whose saves always 400.
    const putEditable = ALL_KINDS.filter((k) => {
      const surface = KIND_REGISTRY[k].editSurface;
      return surface !== 'none' && surface !== 'office-watch';
    });
    expect(putEditable.sort()).toEqual(['text/csv', 'text/html', 'text/markdown']);
  });
});

describe('ARTIFACT_KINDS registry', () => {
  const fake = (): KindDescriptor => ({
    renderer: KIND_REGISTRY['text/markdown'].renderer,
    editSurface: 'none',
    saveMode: null,
    selectionToAI: false,
    preview: 'none',
    label: 'Fake',
  });

  it('a plugin adds an unknown kind and the disposer removes it again', () => {
    const dispose = ARTIFACT_KINDS.register('application/x-acme', fake(), { owner: 'acme.plugin' });
    expect(ARTIFACT_KINDS.get('application/x-acme')?.label).toBe('Fake');
    expect(downloadExtFor({ kind: 'application/x-acme', file_path: 'a/b.acme' })).toBe('acme');
    dispose();
    expect(ARTIFACT_KINDS.get('application/x-acme')).toBeUndefined();
  });

  it('a plugin cannot silently take over a builtin kind (registry conflict, builtin untouched)', () => {
    const original = ARTIFACT_KINDS.get('text/markdown');
    expect(() => ARTIFACT_KINDS.register('text/markdown', fake(), { owner: 'acme.plugin' })).toThrow(RegistryConflictError);
    expect(ARTIFACT_KINDS.get('text/markdown')).toBe(original);
  });

  it('a stale disposer does not clobber a newer registration of the same id', () => {
    const first = ARTIFACT_KINDS.register('application/x-acme', fake(), { owner: 'acme.plugin' });
    const second = ARTIFACT_KINDS.register('application/x-acme', { ...fake(), label: 'Second' }, { owner: 'acme.plugin', replace: true });
    first();
    expect(ARTIFACT_KINDS.get('application/x-acme')?.label).toBe('Second');
    second();
    expect(ARTIFACT_KINDS.get('application/x-acme')).toBeUndefined();
  });

  it('rejects a descriptor whose invariants do not hold, at registration time', () => {
    expect(() => ARTIFACT_KINDS.register('application/x-bad', { ...fake(), saveMode: 'explicit-dirty' }, { owner: 'acme.plugin' }))
      .toThrow(/saveMode must be null exactly when editSurface is 'none'/);
    expect(() => ARTIFACT_KINDS.register('application/x-bad', { ...fake(), preview: 'placeholder' }, { owner: 'acme.plugin' }))
      .toThrow(/previewPlaceholderKey is required/);
    expect(ARTIFACT_KINDS.has('application/x-bad')).toBe(false);
  });

  it('every builtin descriptor passes the same validation a plugin faces', () => {
    for (const kind of ALL_KINDS) {
      expect(() => ARTIFACT_KINDS.register(`probe/${kind}`, KIND_REGISTRY[kind], { owner: 'probe' })(), kind).not.toThrow();
    }
  });
});
