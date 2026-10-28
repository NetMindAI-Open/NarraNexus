# Plugin Platform Glossary

Fixed vocabulary for code, docs and reviews. Use these words and no synonyms.

- **Plugin** — A package that provides implementations for one or more slots and may
  declare new slots. Builtin plugins (`builtin.*`) ship with the host; user plugins are
  installed at runtime on local deployments only.
- **Slot** — A named, contract-bearing extension point identified by a dotted path
  (`turn.pipeline.act.framework`). Has an arity, a contract, an owner and, for `one` slots, a
  default provider.
- **Arity** — `one`: exactly one provider fills the slot at a time (replaceable). `many`:
  any number of providers contribute (additive).
- **Contribution** — What a plugin hands the loader for one slot: a name and a lazy
  factory (`narranexus.kernel.plugins.registry.Contribution`).
- **Registry** — The kernel's name → factory table backing a slot
  (`narranexus.kernel.plugins.registry.Registry`). One per slot path, obtained from the
  `Registries` facade.
- **Hook** — A `many`-arity call point with pluggy semantics (`HookSpec` / `HookImpl`):
  ordered, wrappable, isolated per owner.
- **Binding** — Which provider fills a slot, resolved from six configuration layers
  (turn > agent > env > user config > distribution > default). Never chosen in code.
- **Extension point** — A slot declared by a plugin (manifest `declares`) rather than by
  the kernel; other plugins provide into it the same way.
- **Redeclare** — A plugin that replaces a composite slot listing the child slots it keeps
  alive under the same contract (manifest `redeclares`).
- **Host** — One of the four local processes (`backend`, `mcp`, `workers`, `frontend`)
  a plugin may run in; the manifest's `hosts` field.
- **Activation** — The moment a plugin's code is imported and `activate()` runs; driven
  by activation events (`onStartup`, `onPage:<id>`, ...), never by discovery alone.
- **Contract** — The public Protocol + value types + errors + version + contract tests
  for a kind, in `narranexus.contracts`. Contracts are a leaf package.
- **Kind** — The name of a top-level slot family (`framework`, `provider`, `memory`, ...);
  the key of `API_VERSIONS`.
- **Distribution** — A build-time composition of engine + plugins + branding + auth
  (`narranexus-dist.json`); the official desktop and cloud builds are distributions.
- **Kernel** — The part of NarraNexus without which no process starts: plugin runtime,
  database, settings, identity, supervisor, events, deployment mode.
- **Platform** — The agent runtime and its services; it implements contracts and
  consumes registries but is not itself pluggable.
