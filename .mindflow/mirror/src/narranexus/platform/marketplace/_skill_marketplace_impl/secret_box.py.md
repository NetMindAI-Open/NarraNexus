---
code_file: src/narranexus/platform/marketplace/_skill_marketplace_impl/secret_box.py
last_verified: 2026-08-13
stub: false
---

## 2026-08-13 — decrypt_env_config 全函数化 + decrypt 不再打日志(review round 3)

`decrypt_env_config` 变成**全函数(total)**:`env` 非 dict → 返回空三元组;单个 value 非 str → 计入 `failed`(而不是让 `decrypt` 深处抛 `AttributeError`);blank → 静默跳过(既不注入也不算失败)。目的是让状态查询 `configured_env_var_names` 与凭据注入 `get_all_skill_env_vars` **共用这一个解密器**——前者现在只是 `set(plain.keys())`,不再自带一套 isinstance/try 净化;后者的 `get_secret_box()` 也在调用方包了 try(key 配错时注入空、不拖垮整个 agent 的 skills 贡献)。
`decrypt()` 的 fail-closed 分支**只抛不打日志**——唯一那条带 skill/var 上下文的 ERROR 由注入路径 `get_all_skill_env_vars` 发出(见 `skill_module.py`),不再每次 scan 刷屏;取代了下方 2026-07-22 段记录的「decrypt 解不开时打 ERROR」行为。

## 2026-08-13 — 解密失败 fail-closed(2026-08-01 事故)

新增 `SecretDecryptError`;`decrypt` 对「像 Fernet token(gAAAA)但本 key 解不开」的值从**返回密文改为抛异常**——旧行为让调用方拿密文当凭据跑、下游 opaque 失败(8/1 那 2 个用户)。genuinely-plain 的非 token 值仍原样返回(不误伤)。`decrypt_env_config` 返回值 2→3 元组:`(plain, needs_rewrite, failed_keys)`,解不开的 key **排除出 plain**(密文永不泄给调用方)、列进 failed。key 丢失/轮换的根因是历史(key 曾放未挂载路径),已修;本改动只解决「解不开时的行为」。

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
