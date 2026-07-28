---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/secret_box.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — review 修复:key 落挂载卷 + 解密失败告警

`_default_key_dir` 从 `base.parent/keys`(未挂载,重建即丢)改到 `base/.secrets`(挂载卷内);`decrypt` 解不开时对 Fernet token 打 ERROR(不再静默返回密文)。


# secret_box.py

## 2026-07-20 — get_secret_box() 进程级单例

新增模块级缓存单例,供 `SkillModule.set_skill_env_config` /
`get_all_skill_env_vars` 使用(key 文件每进程读一次)。测试若重定向
`settings.base_working_path`,必须同时把 `_default_box` 重置为 None。

Fernet encryption for skill `env_config` values, replacing the previous
plain-base64 "encoding" in `.skill_meta.json`. Decision locked in spec §7
(marketplace multiplies third-party skills asking users for API keys, and the
Codex symlink-escape gap is not yet fixed — base64 was no longer acceptable).

## Key resolution

1. `SKILL_SECRETS_KEY` env var — cloud deployments MUST inject it (multi-pod
   safe). An invalid value raises immediately: a misconfigured pod should be
   loud, not silently minting a file key that other pods don't share.
2. `~/.nexusagent/keys/skill_secrets.key` — generated on first use, 0600
   (dir 0700). Local/desktop path; the OS user is the security boundary,
   consistent with local auth's trust model. Derived as
   `Path(settings.base_working_path).parent / "keys"`.

## Lazy migration contract

`decrypt()` accepts three shapes: Fernet token → decrypt; legacy plain base64
→ decode; anything else → returned unchanged (never destroy a value we cannot
interpret). `decrypt_env_config()` returns `(plain, needs_rewrite)` —
`needs_rewrite=True` means at least one value was pre-Fernet and the caller
should re-persist via `encrypt_env_config()`. Detection uses the Fernet
version-byte prefix `gAAAA` (`TOKEN_PREFIX`), which no base64 of typical
ASCII secrets produces.

## Gotchas

- Rotating the key file orphans existing tokens — decrypt falls through to
  the base64 branch and returns garbage-or-raw. Key rotation is deliberately
  out of scope for MVP; delete + re-enter secrets if a key is lost.
- `Fernet.generate_key()` output is already urlsafe-base64 bytes; the file
  stores it verbatim (strip() on read tolerates a trailing newline from
  manual edits).
