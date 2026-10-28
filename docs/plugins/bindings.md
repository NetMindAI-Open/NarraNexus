# Bindings — which plugin fills which slot

Every replaceable point of the platform is a **slot** in one tree (`narranexus slots` prints it by domain: kernel, prompt, turn, model, agent, ingress, backend, content, ui). A slot has an arity (`one`: exactly one provider; `many`: an ordered set), a contract, a default provider, and the candidates registered in this process. A **binding** says which candidate is used. Bindings are resolved once at boot and applied everywhere through `narranexus.kernel.plugins.bound` (`bound_entry` / `bound_entries`); change one and restart.

## Layers (later wins)

| Layer | Where | Who |
|---|---|---|
| default | the slot's declared default | the kernel |
| distribution | `narranexus-dist.json` → `bindings` (and `auth` for `kernel.auth`) | distribution author |
| user config | `<plugin home>/narranexus.toml` → `[bindings]` | the user / operator |
| env | `NX_BIND__<slot with __ for .>=provider` | ops |

Distribution-only slots (`kernel.*`, `ui`) accept only the first two layers.

## The config file

```toml
# ~/.narranexus/narranexus.toml
[bindings]
"prompt.assembler" = "acme.brand"                                              # one-arity: a plugin id (or a contribution name)
"prompt.sections"  = ["builtin.prompts:narrative", "builtin.prompts:modules"]   # many-arity: order = effective order, unlisted = dropped
"turn.pipeline.act.framework" = "builtin.frameworks.claude_code"                # the default agent-loop framework
```

`narranexus slots --toml-template` prints a commented file listing every slot with its candidates. `narranexus bind <slot> <provider…>` and `narranexus unbind <slot>` edit the file with validation (unknown slot, unknown provider, distribution-only, conflicts); the snapshot of what a running host resolved is `<plugin home>/run/bindings.resolved.json`, also served at `GET /api/plugin-factory/slots`.

## What honours a binding today

- `kernel.auth` — the authentication provider (backend middleware).
- `turn.pipeline.act.framework` — the default agent-loop framework for agents without an explicit one.
- `prompt.assembler` and `prompt.sections` — how the system prompt is assembled and which sections in which order.
- `many` slots elsewhere expose their entries in registration order unless bound (`bound_entries`); consumers adopt it slot by slot.

## For an agent

The self-extension module gives an agent the same view: `platform_overview`, `platform_slots(domain)`, `contract_docs(kind)`, `agent_self`, and `capability_set` to switch one of its own capabilities.
