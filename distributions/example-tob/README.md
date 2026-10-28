# acme.crm-agent — example ToB distribution

Copy this directory to start your own distribution (or run `narranexus create-app <id>`).

- `narranexus-dist.json` — the declaration: engine range, plugin set (builtins by version range, your plugins by path), excludes, branding, `auth`, defaults, runtime shape, build targets.
- `plugins/acme.crm` — a custom plugin bundled by path; boots with the builtins.
- `plugins/acme.auth-sso` — an `authProviders` stub filling `kernel.auth` (`distributionOnly`: it can only enter at build time, never through the plugin factory).
- `branding/`, `defaults/` — referenced by the declaration.

Check it: `narranexus dist doctor distributions/example-tob`. Lock it: `narranexus dist lock distributions/example-tob`. Run the backend as this distribution: `NARRANEXUS_DIST=distributions/example-tob`.
