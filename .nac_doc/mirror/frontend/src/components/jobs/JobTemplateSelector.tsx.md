---
code_file: frontend/src/components/jobs/JobTemplateSelector.tsx
last_verified: 2026-04-10
---

# JobTemplateSelector.tsx — Two-step wizard for creating jobs from templates

Step 1: pick a preset from `JOB_TEMPLATES` (stored in `jobComplexStore`).
Step 2: fill in variables, preview the dependency graph, submit.

## Why it exists

Allows non-technical users to create multi-job dependency chains without
hand-crafting payloads. The template defines the DAG structure; the user only
fills in the named variables.

## Current status

Not wired into the main `JobsPanel` layout. It is exported and ready but the
trigger UI (e.g., a "New Job" button) does not exist yet. This is intentional
preparatory work.

## Upstream / downstream

- **Upstream:** `JOB_TEMPLATES` constant from `jobComplexStore`, `JobDependencyGraph`
  (preview), `onCreateJobs` callback (provided by the future parent)
- **Downstream:** nothing in production yet

## Gotchas

The icon map (`iconMap`) keys must match the string values in each template's
`icon` field. Adding a new template with an unsupported icon silently falls
back to `Building2`.
