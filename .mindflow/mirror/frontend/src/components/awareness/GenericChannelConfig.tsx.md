---
code_file: frontend/src/components/awareness/GenericChannelConfig.tsx
last_verified: 2026-09-10
stub: false
---

# awareness/GenericChannelConfig.tsx — schema-driven channel panel

## 2026-09-10 — 停用时显示 `disabled_reason`（B-28，复审 M1）

插件频道/通用面板没有 ChannelActiveToggle，此前停用只显示 ` · inactive`；现在 `!enabled` 且 `credential.disabled_reason`
（`ChannelCredentialView` 已声明该可选字段）非空时，下面多一行 `channelActiveToggle.disabledReason`（同一 i18n 键，`data-testid=
channel-disabled-reason`）。Narramessenger 面板没有启停 UI，本轮不接。

## Intent

Renders a channel's credential schema (GET /api/channels/{channel}/schema) as the bind form — secrets as password inputs, selects, booleans, required check — and, once bound, the public identity fields with test (when supported) / activate-deactivate / unbind through the generic API; a webhook channel shows its inbound path. What a channel plugin's Channels row renders (`makeGenericChannelConfig`); the six builtins keep their bespoke panels until 4d.

## 2026-09-04 · bind form from `bind_fields` (batch 4d.3)

The form renders `schema.bind_fields` (what a bind call takes), the bound view still lists `schema.fields` identity values — the two differ for builtin channels, so a manager-backed channel rendered generically shows the right inputs.
