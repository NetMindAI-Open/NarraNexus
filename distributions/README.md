# Official distributions

Each directory holds one `narranexus-dist.json` (spec section 19). `desktop` and `cloud` are what we ship; `minimal` is the smallest useful base; `example-tob` is the copy-me sample for a third-party distribution (custom plugin + SSO auth stub).

- Validate: `narranexus dist doctor <dir>` — engine range, plugin ranges, bundled manifests, dependency closure, `auth` provider, bindings, size budget; exits non-zero on any problem.
- Lock: `narranexus dist lock <dir>` writes `narranexus-dist.lock.json` (the exact plugin set a build bakes in).
- Run: a host started with `NARRANEXUS_DIST=<dir>` boots that plugin set — builtins outside it are dropped, bundled plugins boot in stage 1, `runtime.userPlugins=false` skips the plugin registry.
