---
code_file: frontend/src/components/artifacts/renderers/__tests__/HtmlRenderer.test.tsx
last_verified: 2026-05-14
stub: false
---

## 2026-05-14 — pointer model: drop `version` prop, mock `getRawUrl`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The renderer no longer accepts a `version` prop and the iframe `src` is set
asynchronously from the view-token endpoint. The test mocks
`artifactsApi.getRawUrl` to return a known token URL, then `waitFor`s the
iframe to mount before asserting the sandbox attribute and `src`.

# HtmlRenderer.test.tsx — Security regression test for HtmlRenderer sandbox

## Why it exists

The iframe `sandbox` attribute in `HtmlRenderer` is a load-bearing security control. Accidentally adding `allow-same-origin` (or other dangerous flags) would silently break the isolation model. This test makes that regression visible immediately.

## Upstream / Downstream

- **Tests**: `HtmlRenderer.tsx` exclusively.
- **Runner**: No test runner configured in `package.json` at time of writing. The file uses vitest-style imports; it will be auto-discovered when `vitest` + `@testing-library/react` are added to devDependencies.

## Design decisions

**Two assertions, not one.** The positive assertion (`toContain('allow-scripts')`) verifies the feature works (JS is allowed). The negative assertions (`not.toContain(...)`) verify the security invariants hold. Both are needed: a future dev removing `allow-scripts` would break interactivity, and a dev adding `allow-same-origin` would break isolation.

**Hard-coded fake artifact.** No mocking framework needed — the component only reads `artifact.agent_id`, `artifact.artifact_id`, and `artifact.title` from the prop. The fake is minimal and explicit.

## Gotchas

**Tests are documentation-as-code until a runner is added.** They cannot be run as-is. The first person to add vitest will find them auto-discovered and should confirm they pass before merging.
