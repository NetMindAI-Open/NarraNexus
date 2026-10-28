---
code_file: src/narranexus/platform/module_system/identity/verify.py
stub: false
last_verified: 2026-08-10
---

## Why it exists

The ONE header-level identity verification (PR #260 review Important #3):
mcp middleware and backend's nx-agent service path ran the identical
algorithm (explicit header or the bearer identity_token field → Ed25519 verify → cross-check
bearer user_id vs proven sub) as two near-identical copies, with a third
consumer already forecast. Copies drift; the algorithm now lives here once.

## The split that matters

Key-availability POLICY is deliberately NOT here. Whether a missing public
key fails open (mcp — data plane, a keyless deploy has no signer either) or
fails closed (backend — an nx-agent bearer is always a service call) is the
one real difference between the verifiers, so each caller loads the key and
chooses its own degradation; this function only answers "given this key, is
this proof good?" with a stable reason string for their logs.

## The mismatch rule

自述 user_id 与 sub 不符 = 伪造字段,整条记录不可信。**两条运载通道
（显式 `X-NarraNexus-User-Id` 头、bearer 第 5 段）各自独立判**——消费方
优先读显式头、bearer 只是回落,只验一条就等于给伪造者留了另一条没人查的
通道;两条都钉死,未来读取方偏好变化也开不回这个口子。占位符串不算声明
（与读取方同语义）。reason 里的自述值截断 64——它会进日志和 401 响应体。

## 上下游

- 消费方: [[mcp_auth]] `IdentityAuthMiddleware` / `verified_caller_for_tool_call`,
  [[../../backend/auth.py|backend/auth]] `_verify_nx_service_bearer`
- 依赖: [[tokens]] `verify_identity_token` + `_mcp_identity`
  (`parse_bearer_identity` 公开面 + 包内 `_explicit_header`/占位符语义)
