---
code_file: tests/marketplace/test_install_pipeline.py
last_verified: 2026-09-09
stub: false
---

# test_install_pipeline.py

End-to-end pipeline tests on a real (tmp) filesystem + in-memory DB.
The `workspace` fixture isolates `settings.base_working_path`, resets the
SecretBox singleton, and replaces `backup_after_api_install` with a recorder
(the real one writes under the developer's home directory).

Key scenarios: happy zip install asserts every meta field the pipeline adds
(hash/content_hash/updated_at/version) plus the audit row and backup call;
the malicious-zip test proves the gate fires BEFORE `skills/` is touched
(no dir, no audit row, no backup); replace proves env_config survives an
upgrade in decryptable form; github flow is tested by stubbing
`fetch_github_repo` (no network in tests); the two SecretBox tests pin the
storage format (Fernet `gAAAA` prefix, never plaintext/base64) and the lazy
migration rewrite of legacy base64 values.

## 2026-09-09（复审 C3）— 多 skill 仓部分失败 + 同仓依赖顺序

`test_github_multi_skill_repo_isolates_a_rejected_sibling`：三个根、中间那个被扫描门拒 → 另外两个 installed、
失败项带 `skill_name`/`error`、盘上没有 evil、审计两行；`…installs_same_repo_dependencies_first`：alpha 依赖同仓
beta，名序会先装 alpha，结果顺序必须 beta→alpha。

## 2026-09-09 — GitHub 多 skill 仓用例

`_fake_fetch` 现在按新签名返回 `([roots], url)`；新增
`test_github_multi_skill_repo_installs_each_skill`：两个 `skills/<name>/SKILL.md` → 两个
installed 结果、各自 meta 带同一 source_url、审计表两行（按 `skill_id`）。

