---
code_file: src/xyz_agent_context/agent_framework/transcription/resolver.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 (review 二轮加固) — 门禁叠加预算检查

`_user_has_free_tier` 从"行存在即 True"改为"行存在 **且** `qs.check()` 有
余量"。动因：STT 走 system-default NetMind 凭证时**不从 user_quotas 扣费**，
旧实现下耗尽账号可无限烧运营方 STT key，而同一用户的 LLM 路径已被
QUOTA_EXCEEDED 挡住——两条路必须共享同一预算裁决。测试
`test_exhausted_quota_gets_no_system_default` 钉住。

## 2026-07-18 — Tier 5 门禁从"偏好开关"改为"是否授予了免费额度"

`_user_opted_in_to_free_tier` 改名 `_user_has_free_tier`，判断从
`quota.prefer_system_override`（已重定义为耗尽通知闩锁，见
[[provider_resolver]]）改为 **quota 行是否存在**（行即授予）。这是免费额度
偏好删除时差点漏掉的消费者——留着旧判断会让闩锁 fired 的用户被错误地拒掉
STT 免费层。下文 (c) 一段的旧描述自此为历史记录；"无 quota 行不隐式计费
运营方"的守卫保留。

# resolver.py — ordered candidate list for transcription

## Why it exists

The user's "OpenAI 配了就用 OpenAI、NetMind 配了就用 NetMind" requirement
means we walk the user's existing provider rows in priority order —
this module is the priority. It reads from `UserProviderService` (same
DB rows the chat/embedding LLM resolver uses) and the cloud-default
settings, never asking the user to configure transcription separately.

## Priority (high → low)

1. user OpenAI official (`api.openai.com`) — best Whisper quality + most
   stable contract
2. user NetMind (`*.netmind.ai`) — converted from the OpenAI-aggregator
   base_url (`/inference-api/openai/v1`) to the native root
   (`https://api.netmind.ai`); same key works for both
3. user other OpenAI-multipart compatible (Yunwu, self-hosted whisper.cpp)
4. legacy `settings.openai_api_key` — local mode only; in cloud the
   operator's own key must NOT silently transcribe random users' audio
5. system-default NetMind from `settings.system_default_netmind_*` —
   cloud free tier. Three preconditions, ALL required:
   (a) `public_base_url` is configured;
   (b) `SystemProviderService.is_enabled()` (i.e. cloud mode + free
       tier env vars set);
   (c) `user_quotas.prefer_system_override` is True for this user.

   The (c) gate matters: it's the SAME "Use free quota" toggle that
   chat / embed / helper_llm respect via `provider_resolver.py`. Without
   this check, a user who explicitly opted out of the free tier (e.g.
   to keep the operator from billing them via the system NetMind key)
   would still see STT silently route through it. New users with no
   quota row at all are treated as opted-out — onboarding has to
   create the row before STT will use the free tier.

## Why no Tier 0 for system-default OpenAI

Cloud free tier intentionally rides on NetMind only — NetMind's per-
minute audio cost is low enough that we don't meter it. Putting OpenAI
in there would either (a) burn quota the user doesn't see, or (b)
require teaching cost_tracker about transcription, which it doesn't
currently know.

## Skipped

- **OpenRouter** (`*.openrouter.ai`) — Whisper exposed as JSON+base64,
  not multipart; no backend yet. Keeping it in the user_provider table
  is fine — the resolver just walks past.
- **NetMind's anthropic-protocol provider** — only the openai-protocol
  one carries credentials usable for `/v1/generation`. The user's
  one-key card creates both protocols linked; we look at the openai
  side.
- **All NetMind candidates when `PUBLIC_BASE_URL` is empty** —
  whether user-configured (Tier 2) or system default (Tier 5).
  NetMind's `/v1/generation` worker accepts only an http/https
  `audio_url` it can GET — we verified via probe that base64,
  multipart, and data URIs are all rejected. Tauri / `bash run.sh`
  desktops behind NAT have no public ingress, so the credential is
  decorative there. Skipping cleanly avoids "transcript silently
  empty" failures and lets the frontend dialog drop the NetMind
  branch via the `none_openai_only` reason. Self-hosted backends
  with their own VPS just set PUBLIC_BASE_URL and NetMind comes
  back online — same gate, no mode-specific code path.

## Gotchas

- `is_active` is checked on every provider — disabled providers are
  invisible to transcription, just like to chat.
- The resolver swallows DB / import errors during user-tier lookup
  (logs to `logger.debug`). Upload route never sees the exception;
  transcription degrades through to `settings.openai` / system default.
- The NetMind credential's `base_url` is rewritten from the user's
  configured aggregator URL (`https://api.netmind.ai/inference-api/openai/v1`)
  to the native generation root (`https://api.netmind.ai`). The
  generation API does NOT live at the aggregator path.
