# Local auth (`builtin.auth.local`)

Authentication provider of the desktop/local distribution: the X-User-Id header names the user (authProviders, distribution-only).

A NarraNexus builtin plugin, packaged as `narranexus-plugin-auth-local` (plugin platform batch 6c). It fills the distribution-only slot `kernel.auth`; a distribution selects it with `"auth": "builtin.auth.local"` in `narranexus-dist.json`. It cannot be installed at runtime (`distributionOnly`).
