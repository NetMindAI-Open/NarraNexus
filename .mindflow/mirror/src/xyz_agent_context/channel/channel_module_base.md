---
code_file: src/xyz_agent_context/channel/channel_module_base.py
stub: false
last_verified: 2026-05-08
---

## Why it exists

Phase 2 of the IM channel abstraction. Captures the structural
boilerplate every IM Module needs (sender registry self-registration,
``hook_data_gathering`` template, MCP server creation glue) without
constraining each channel's product-surface decisions (LLM
instructions, MCP tool count/shape, credential schema).

Pairs with Phase 1's ``ChannelTriggerBase`` to give Slack/Telegram in
Phases 3/4 a complete "fill in the blanks" experience for adding a new
IM integration: subclass two bases + write platform-specific content.

## Design decisions

- **Mechanism only, no content.** ``get_instructions`` is abstract —
  Lark writes 600 lines (three-click flow + iron rules + identity
  guide), Telegram might write 150 lines (no admin approval, no
  identity model). Same with MCP tools and credential schema. The
  base does not impose a shape.

- **Sender registers exactly once per channel.** Class-level
  ``_sender_registered_for_channel`` dict guards against double
  registration when multiple subclass instances are constructed in
  the same process (one per agent).

- **``build_extra_data(cred, ctx_data)``, not just ``cred``.** Lark's
  ``is_owner_interacting`` trust signal depends on the current
  channel_tag in ``ctx_data.extra_data``, derived per turn. The
  signature carries ``ctx_data`` so subclasses with similar
  per-turn-derived fields work without contortions.

- **``hook_after_event_execution`` filter uses both enum and string
  comparison.** Python 3.11+ changed ``str(enum_member)`` to return
  the qualified name; ``str(WorkingSource.LARK) == "lark"`` is False.
  The base uses direct ``ws == self.working_source or ws == self.working_source.value``
  to handle both serialization shapes (``WorkingSource`` inherits
  ``(str, Enum)`` so member equality with the string value works).

- **``create_mcp_server`` returns None on import error.** A stripped
  image without ``fastmcp`` installed should still boot — the rest of
  the channel runs without agent-callable tools.

- **Get-then-insert idempotency** (inherited from ChannelInboxWriter
  pattern): ``hook_data_gathering`` swallows credential-load
  exceptions and logs a warning; the agent loop's ability to gather
  context for OTHER modules must not break because Lark's DB hiccupped.

## Upstream / downstream

- **Upstream**: ``LarkModule`` (Phase 2). Phase 3 ``SlackModule`` and
  Phase 4 ``TelegramModule`` will both subclass.
- **Downstream**:
  - ``XYZBaseModule`` — superclass; provides ``agent_id``, ``db``,
    ``mcp_host``, ``get_mcp_db_client``.
  - ``ChannelSenderRegistry`` — registry the base self-registers
    into.
  - ``mcp.server.fastmcp.FastMCP`` — lazy-imported only when
    ``create_mcp_server`` is called.

## Gotchas

- Subclass MUST set ``channel_name`` AND ``ctx_data_key``. The
  ``__init__`` raises ``ValueError`` if either is empty — caught at
  module instantiation, not at runtime, so it surfaces via the
  framework's startup path.
- ``_sender_registered_for_channel`` is class-level on
  ``ChannelModuleBase``, not on each subclass. This means multiple
  subclasses (e.g. LarkModule + future SlackModule) share the same
  guard dict but use different keys (``channel_name``), so they don't
  interfere.
- ``XYZBaseModule.get_config`` is abstract. The base does NOT provide
  a default — each subclass writes its own (priority, description,
  module_type vary per channel).
