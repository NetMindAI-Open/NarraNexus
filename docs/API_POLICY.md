# NarraNexus Plugin API Stability and Deprecation Policy

## 1. Scope

This policy covers everything exported from `narranexus.contracts` (Python) and, once
published, `@narranexus/contracts` (TypeScript); the plugin manifest schema
(`narranexus-plugin.json`); the slot tree (`docs/plugins/slots.md`); and the hook
specifications. Nothing else is a public API. Imported names, private (`_`-prefixed)
modules, log text, iteration order and timing are explicitly NOT part of the contract
(Hyrum's Law: we do not promise what we did not declare).

### 1.1 What a builtin plugin may additionally import

The 29 `builtin.*` packages under `plugins/` are shipped from this repository and
released with the engine, so they are allowed one thing a third-party plugin is not:
they may import **public** modules of `narranexus.platform` (the database stack, the
repositories, the schemas, the module system). That is an engine-internal coupling
under a repo-lockstep guarantee — the two move in the same commit — not a promise to
anyone outside this repo, and it is why each builtin's `pyproject.toml` declares only
`narranexus-contracts` + `narranexus-sdk`: those are its *contract* dependencies, while
the platform is the engine that hosts it.

Two lines are enforced rather than documented, by import-linter contracts in
`pyproject.toml`:

- **`plugins never import the host (backend)`** — a plugin may not import `backend.*`
  at all. What an HTTP route legitimately needs from the host (who is calling, may they
  touch this agent, the upload ceiling, the SSRF egress screen, an artifact view token)
  is `narranexus.contracts.web.WebHost`, published on the service locator by
  `backend/plugin_sdk_host.py` and called through `narranexus.sdk.web`. Its
  `ignore_imports` list is empty and must stay empty: an entry there is a builtin
  privilege a third party cannot reproduce.
- **`plugins never import a private platform module`** — an `_`-prefixed platform
  module is private even inside the platform. When a plugin genuinely needs one of its
  functions, the fix is a named public function on the owning package's facade (as with
  `module_system`'s caller-identity resolvers, `marketplace`'s store/pipeline/secret-box
  seam, `agent_framework.llm.prompt_probe_emit` and
  `agent_framework.adapters.build_tool_policy_guard`), never an exemption.

Both contracts run with `unmatched_ignore_imports_alerting = "warn"` so a stale
exemption is reported instead of silently outliving the debt it covered. (The
setting used to be `"none"` on the two older contracts, which is how six ignores
for `backend/routes/channels/{slack,telegram,discord}` — routers that moved into
plugin packages a batch earlier — survived as permanent holes nobody could see.)

### 1.2 How to write an exemption

Every entry in an `ignore_imports` list, a `per-file-ignores` entry, or any other
"this rule does not apply here" switch carries three things, in a comment
immediately above it:

1. **What** the exception covers and **why it is legitimate** — not "temporary",
   but the actual structural reason.
2. **The tracked item** that will remove it.
3. **`expires YYYY-MM-DD`**.

The model is the TID251 exemption for `plugins/builtin.nexus_plugins_module/**`
in `pyproject.toml`: the factory IS the host's registry UI packaged as a plugin,
so it is the one plugin allowed to read and mutate the registry; the follow-up
that removes it is "factory host API"; it expires 2026-12-31. An exemption
without an expiry is a rule quietly deleted.

## 2. Stability levels

Since plugin platform batch 6 (2026-09) every *manifest* kind in
`narranexus.contracts.API_VERSIONS` is **stable**: `STABILITY` says so,
`scripts/dev/api_check.sh` (griffe) diffs the public surface of `narranexus.contracts`
and `narranexus.sdk` against the base branch in CI, and a breaking change must go
through section 4.

Two entries are deliberately **alpha**, and will stay alpha until they have survived a
release cycle:

- **`web`** — `narranexus.contracts.web` (the request-scoped `WebHost` a plugin router
  calls). Landed in batch 6c; the ownership-vs-visibility split (`require_agent_owner`
  vs `agent_visible`) is still under review, and freezing it now would freeze a
  judgement we have not finished making.
- **`channel_authoring`** — the channel base classes `narranexus.sdk` re-exports
  (`ChannelModuleBase`, `ChannelContextBuilderBase`, `WebhookChannelTriggerBase`,
  `ParsedMessage`, `ChatType`, `MessageContentType`, `ModuleConfig`, `WorkingSource`,
  `GenericCredentialStore`). They exist as SDK exports so the griffe gate SEES them
  change — `templates/channel` scaffolds third-party code on top of them and previously
  imported them from `narranexus.platform.*`, where no gate was watching. The channel
  framework itself landed one batch ago and `GenericCredentialStore` is a tracked
  follow-up (a channel module should ask the host for its credential, not construct the
  store), so grading them stable to look tidy would promise a deprecation window we
  cannot honour.

Every exported symbol and every slot carries exactly one level, declared in code
(`narranexus.contracts.STABILITY`, `Slot.stability`) and in the generated docs.

- **alpha** — May change or be removed in any release without notice. A NEW kind starts
  here until it is promoted in `narranexus.contracts.STABILITY`.
- **beta** — Feature-complete and a candidate for stable. Backwards-incompatible changes
  are allowed only after a deprecation period of at least **2 minor releases or 90 days,
  whichever is longer**, declared at the time the symbol is marked beta.
- **stable** — No backwards-incompatible change within the same major version of the
  contracts package. Removal requires a new major version.

## 3. What counts as a backwards-incompatible change

Any of the following on a beta or stable symbol is incompatible and requires the
deprecation process (beta) or a major version bump (stable):

- Removing or renaming a symbol, method, field, hook, slot, or enum value.
- Changing the type of an existing field or parameter.
- Making an optional parameter or field required, or changing a default value.
- Changing documented semantics, error types, or ordering guarantees.
- Moving a symbol to a different module without leaving a re-export.

Additive changes (new optional fields, new methods with defaults, new hooks, new slots,
new enum values on inputs) are compatible. Extension points are only ever added; a
breaking change to a slot's contract ships as a new slot, never as an edit.

## 4. Deprecation process

1. Mark the symbol with `warnings.deprecated` (PEP 702) / a JSDoc `@deprecated` tag naming
   `since`, `removal` and the replacement, and list it under "Deprecated" in the
   release notes (`docs/RELEASE_NOTES.md`, one section per version).
2. The host emits a `DeprecationWarning` once per process per symbol and records a
   `warnings` entry in the plugin load report so the plugin factory can show it.
3. The replacement must be available in the same release the deprecation is announced.
4. A deprecated API may not be replaced by a less stable one.
5. Old and new forms must round-trip: data written through the new form must be readable
   through the old form during the deprecation window.
6. After the window, removal happens only in a release whose notes list it under "Removed".

### Open deprecation windows

| Since | Removal | Rule | Migration |
|---|---|---|---|
| 1.20 | 1.21 | A manifest's `api` must version **every** contract kind of the slots it provides into or declares (`Slot.kind`). Today an uncovered kind is a `warnings` entry on the plugin load report; from 1.21 it is a `ManifestError` and the plugin is rejected. | Add the missing kind to the manifest's `"api"` object at the current version (e.g. `"framework": 0`). `narranexus plugin doctor` names them. Builtins are already refused at parse time — they are the host's own code and the template third parties copy. |

## 5. Versioning

- `narranexus.contracts.API_VERSIONS[kind]` is the integer contract version per kind; a
  breaking change bumps it. The host declares what it supports; a plugin manifest declares
  what it needs in `api`; a mismatch fails closed at load time.
- New optional capabilities are negotiated (`capabilities()` on drivers, manifest fields
  with defaults) rather than by bumping the major version.
- Feature flags are a rollout tool, not a compatibility promise; they never substitute for
  this policy.

## 6. Exemptions

Security fixes may shorten or skip the deprecation window; the release notes must say so
explicitly.

## 7. Route prefixes: builtin vs third-party

A builtin plugin's routers keep the absolute prefixes the product always had
(`/api/agents/...`, `/api/jobs`, `/api/skills`, `/api/teams`, ...): the frontend and
every client address them there, and a builtin is the product. A third-party
plugin's routers are confined to `/api/x/<plugin id>` (the host refuses anything
else). This is the one deliberate difference between the two; both are declared
the same way (`backend.routes` in the manifest, a `RouterSpec` in the plugin's
own package). Nothing under `backend/routes/` belongs to a plugin any more.

## 8. Naming conventions for contribution symbols

A manifest's `provides` names module-level symbols; the vocabulary is fixed so a
reader can tell from the name what a symbol holds and a template composes
without renaming:

- A **many-arity** slot is filled by an UPPER_SNAKE plural noun holding a tuple
  of `Contribution`: `ROUTES`, `TABLES`, `WORKERS`, `SETTINGS`, `TOOLS`,
  `MCP_SERVERS`, `BUNDLES`, `SKILLS`, `RECALL_STRATEGIES` (per stage:
  `<STAGE>_STRATEGIES`), `PROFILES`, `CONTEXT_PROVIDERS`, `TRIGGERS`, `MODULES`,
  `CHANNEL` (one descriptor per channel plugin). Builtin plugins use
  `CONTRIBUTIONS` when the plugin is the slot's whole builtin set (e.g. the
  prompt sections) — and every module filling the SAME many-arity slot uses the
  same spelling: the nine `model.providers` modules are all `CONTRIBUTIONS`.
- A **one-arity** slot is filled by a single `Contribution` named `CONTRIBUTION`
  (a framework driver, the prompt assembler, the pipeline). When one module
  fills SEVERAL one-arity slots — a framework's own seats, e.g.
  `turn.pipeline.act.framework.nexus_power.{stop,compaction,projector,expression}`
  — the seat name prefixes the word: `STOP_CONTRIBUTION`, `COMPACTION_CONTRIBUTION`,
  …, and the many-arity sibling is `POLICY_CONTRIBUTIONS`. The suffix still says
  the arity, which is the point of the rule.
- `backend.hooks` names `HOOKS`: a tuple of `@hookimpl(name)` functions.
- A framework plugin's static facts live in `META` (a `FrameworkMeta`) and are
  carried as `Contribution.meta["framework"]`.
- The registered **name** of a contribution is what other plugins refer to (a
  profile names a strategy, a binding names `plugin:name`): lower_snake, unique
  within the slot, prefixed with the plugin package name for third-party
  contributions (`acme_weather_recall`) so two plugins never collide.

Renaming a symbol a manifest names is a breaking change for that plugin only
(the manifest is edited with it); renaming a contribution **name** is a breaking
change for everyone who binds or references it and follows §4.
