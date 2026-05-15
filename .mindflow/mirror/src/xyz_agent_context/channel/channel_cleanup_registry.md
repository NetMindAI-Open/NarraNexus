---
code_file: src/xyz_agent_context/channel/channel_module_base.py
stub: false
concept: true
last_verified: 2026-05-09
---

## Why it exists

This is a **concept** mirror md — there is no separate
``channel_cleanup_registry.py``. The "registry" lives implicitly as a
virtual method (``cleanup_for_agent``) on ``ChannelModuleBase`` plus
the dynamic walk in ``backend/routes/auth.py:delete_agent`` that
iterates every ``ChannelModuleBase`` subclass in ``MODULE_MAP``. The
combination IS the registry.

This pattern was added in Phase 4 (Task 14) to fix Phase 3 lesson #4:
``delete_agent`` had no Slack cascade, leaving orphan
``channel_slack_credentials`` rows, orphan ``bus_channel_members``
entries, and ghost replies from a long-poll loop that didn't know its
agent was gone. Inline channel-specific blocks in ``delete_agent``
were fragile — every new IM channel meant another edit to that
function. Registry-driven walk replaces them.

## Design decisions

- **Each channel owns its cleanup, not the auth route.** ``delete_agent``
  used to carry inline ``DELETE FROM lark_credentials WHERE agent_id =
  ...`` style blocks, which meant the auth route grew with every new
  channel and you could ship a new IM channel that "worked" but
  silently leaked rows on agent delete. Now adding a channel requires
  zero edits to ``delete_agent``.
- **Default implementation handles 90% of channels.** Walks
  ``bus_channel_members`` for ``channel_id LIKE "{channel_name}_%"``,
  drops empty inbox channels, deletes the credential row from
  ``channel_{channel_name}_credentials``. Slack and Telegram inherit
  unchanged.
- **Lark overrides** in ``lark_module/lark_module.py:cleanup_for_agent``.
  Lark needs CLI-profile teardown (release Keychain references via
  ``LarkCLIClient.profile_remove``) and workspace-directory removal
  (``cleanup_workspace`` under ``HOME``) BEFORE database cleanup —
  the CLI subprocess holds DB / Keychain handles that block deletion.
  Override calls ``super().cleanup_for_agent(...)`` last to chain the
  default DB cleanup.
- **``_credential_table_name`` virtual hook for schema deviations.**
  Lark predates the ``channel_*_credentials`` naming convention; its
  table is just ``lark_credentials``. The hook lets each channel
  declare its table name without forcing migration.
- **Walks ``MODULE_MAP``, not a separate registry list.**
  ``MODULE_MAP`` is the ground truth for "which Modules exist". The
  walk filters with ``issubclass(cls, ChannelModuleBase)``. No
  duplicate registry that could drift out of sync with the module
  loader.
- **Per-channel exception isolation in the walk.** One channel's
  cleanup raising must NOT abort the others — the auth route catches
  per-channel exceptions, logs at WARN, continues. Partial cleanup
  is better than no cleanup.
- **Returns ``{table_name: count}`` stats, merged into the
  ``delete_agent`` response.** Operators see exactly which tables
  were touched. Empty dict means "no rows for this agent / this
  channel" — silent success.
- **Agent's row deleted last.** The walk runs BEFORE
  ``DELETE FROM agents``. Reverse order would fail the
  channel-cleanup look-ups (they query agent's credential row).

## Upstream / downstream

- **Defined on**: ``ChannelModuleBase.cleanup_for_agent`` (default
  implementation) + ``ChannelModuleBase._credential_table_name``
  (virtual hook).
- **Overridden by**: ``LarkModule.cleanup_for_agent`` (CLI profile +
  workspace dir teardown).
- **Inherited unchanged by**: ``SlackModule``, ``TelegramModule``.
- **Walked by**: ``backend/routes/auth.py:delete_agent`` (loop over
  ``MODULE_MAP`` filtered to ``ChannelModuleBase`` subclasses).

## Gotchas

- Forgetting to call ``super().cleanup_for_agent(...)`` in an
  override silently leaks DB rows. Lark's override puts it last —
  follow that pattern.
- A channel that doesn't follow the ``{channel_name}_*`` channel_id
  prefix convention for ``bus_channel_members`` will leak its inbox
  rows under the default cleanup. New channels adopt or override.
- Re-runs are idempotent (every cleanup checks ``cred = await
  db.get_one(...)`` first and no-ops if gone) — but a new subclass
  that throws at ``__init__`` is caught + logged in the walk and
  silently leaks that channel's rows. Keep ``__init__`` cheap.
