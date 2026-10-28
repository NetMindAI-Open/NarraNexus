# NetMind auth (`builtin.auth.netmind`)

Authentication provider of the cloud distribution: the NetMind-issued JWT bearer token names the user (authProviders, distribution-only).

A NarraNexus builtin plugin, packaged as `narranexus-plugin-auth-netmind` (plugin platform batch 6c). It fills the distribution-only slot `kernel.auth`; a distribution selects it with `"auth": "builtin.auth.netmind"` in `narranexus-dist.json`. It cannot be installed at runtime (`distributionOnly`).
