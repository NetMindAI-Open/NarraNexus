# Prompts (`builtin.prompts`)

The system prompt as a plugin: this package fills `prompt.sections` with the five default sections (security iron rules on cloud, the user's temporal context, the main narrative, the module instructions, the first-run bootstrap injection) and `prompt.assembler` with the default joiner.

Replace or reorder without touching the platform — in `narranexus.toml` (or a distribution's `bindings`):

```toml
[bindings]
"prompt.sections" = ["builtin.prompts:narrative", "builtin.prompts:modules", "acme.brand:persona"]  # order = prompt order; unlisted sections are dropped
"prompt.assembler" = "acme.brand"                                                                   # a plugin providing its own PromptAssembler
```

A plugin adds a section by providing a `PromptSectionProvider` (`id`, `order`, `budget_chars`, `async render(ctx)`) under `prompt.sections`; the assembler logs sections over their declared budget.
