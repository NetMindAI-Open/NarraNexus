---
code_file: src/xyz_agent_context/repository/invite_code_repository.py
last_verified: 2026-05-14
stub: false
---

# invite_code_repository.py — 邀请码数据访问层

## 为什么存在

云端注册门禁从"单一全局 `INVITE_CODE` 环境变量"换成"DB 里每个码唯一、用后
即焚"。这个 repo 是 `invite_codes` 表的唯一数据访问入口，封装 Mode B（自动
发码 + 全局上限 200 + waitlist）需要的所有读写。

## 上下游关系

- **被谁用**：
  - `backend/routes/invite.py` — `/api/invite/request`：`list_for_email`
    （幂等判断）、`count_active`（cap 判断）、`create`、`mark_email_sent`
  - `backend/routes/auth.py` — `register()`：`get_by_code`（快速校验）+
    `consume`（原子消费）+ `revert_consume`（user insert 失败回滚）
  - `backend/routes/admin_invite.py` — `list_all` / `promote` / `revoke`
- **依赖谁**：`invite_code_gen.generate_code`、`utils.timezone.utc_now`、
  `schema.InviteCode`、`BaseRepository`。

## 设计决策

- **`consume` 是单条带条件 UPDATE**（`WHERE code=? AND status='issued'`）——
  这是并发抢码的 race guard。两个注册请求抢同一个码，只有一条 UPDATE 影响
  到行，另一条 affected=0。调用方据 affected==1 判断"我是消费者"。
- **`create` 的唯一性靠 DB 约束 + 重试**——`generate_code` 不保证唯一，
  `UNIQUE(code)` 约束才是 source of truth。insert 撞了就换码重试（最多 8
  次，2^39 keyspace 下基本不会触发）。
- **`count_active` 只数 `issued + used`**——`waitlisted` / `revoked` 不占
  名额。Mode B 的 cap（默认 200）对的就是这个数。
- **`revert_consume` 存在的理由**：register 流程里"消费码"在"insert user"
  之前/并发处发生，若 user insert 失败要把码退回 `issued`，否则码被白烧。

## Gotcha

- `id_field = "code"` —— 这个表的业务主键是 `code`（虽然也有自增 `id`）。
  `BaseRepository.get_by_id` 因此按 code 查，符合直觉。
- 时间戳统一存 `YYYY-MM-DD HH:MM:SS` 字符串（`_now_str()`），对齐 schema
  的 `(datetime('now'))` 默认值格式，SQLite TEXT / MySQL DATETIME 都吃。
- `revoke` 对 `used` 码无效——账号已建，撤码没意义。
