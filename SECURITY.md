# Security Policy

## Supported versions

We support the latest minor release of NarraNexus. Older minor
versions do not receive backported fixes — please upgrade.

| Version       | Supported |
| ------------- | --------- |
| Latest `v1.x` | ✅        |
| Earlier `v1.x`| ❌        |

## Reporting a vulnerability

**Please do not open a public issue for vulnerabilities.**

Use GitHub's private vulnerability reporting:

> [Report a vulnerability →](https://github.com/protagolabs/NarraNexus/security/advisories/new)

Or email the maintainers directly — see [`MAINTAINERS.md`](./MAINTAINERS.md)
for the current contact address.

## What to include

A useful report contains:

- The component affected (e.g. `backend/routes/auth.py`,
  `xyz_agent_context.agent_runtime`, the Tauri sidecar, …).
- A minimal reproduction — request payload, env config, or steps.
- Impact: what an attacker can read, write, or trigger.
- Suggested fix if you have one (not required).

## Response targets

- **Acknowledgement** within 72 hours of report.
- **Initial assessment** within 7 days.
- **Remediation plan** within 14 days for high-severity issues
  (auth bypass, RCE, secret leakage). Lower-severity issues are
  triaged on the normal release cadence.

We will keep you in the loop and credit you in the release notes if
you wish.

## In-scope areas

- Authentication & session handling (`backend/routes/auth.py`,
  `backend/auth.py`)
- Identity resolution in API routes (X-User-Id / JWT)
- Bundle export — what credentials and chat content can leave
  (`src/xyz_agent_context/bundle/`)
- LLM prompt-injection paths that lead to data exfiltration or
  unauthorized tool calls
- Local-mode WebSocket auth (`/ws/agent/run`)
- Tauri sidecar privilege escalation or path escapes

## Out of scope

- Issues that require a malicious local OS user with file-system
  access — local mode's security boundary is the OS user, by design
- Volumetric denial of service against a self-hosted instance
- Findings in third-party dependencies without a NarraNexus-specific
  amplification path — please report those upstream
- Findings against the cloud deployment at `agent.narra.nexus`
  unrelated to the open-source code (operational issues belong to the
  hosting team, not this repo)

## Disclosure

We follow coordinated disclosure. After a fix lands and users have
had a reasonable window to upgrade, we publish a brief advisory
summarizing the issue, the fix, and any required user action.
