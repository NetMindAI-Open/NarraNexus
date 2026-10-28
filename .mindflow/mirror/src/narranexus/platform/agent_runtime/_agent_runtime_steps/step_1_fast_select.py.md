---
code_file: src/narranexus/platform/agent_runtime/_agent_runtime_steps/step_1_fast_select.py
stub: false
last_verified: 2026-08-21
---

## 2026-08-21 — 桶锚点不复用（独立审查 Important #3 的修复端）

`load_narrative_from_db(anchor_id)` 之后加一道 `is_reusable_anchor()` 检查
（定义在 [[narrative_service.py]]）：legacy default 桶锚点按"锚点行已消失"
处理——落回无锚重探，durable chat 随后 create_fast 并把 session 锚改写到新线。
没有这道检查时，快路径会把仍锚在桶上的 session 每轮重新钉回桶里（无 judge、
无自愈出口），把慢路径 slice 5 的拒绝整个抵消掉。测试：
tests/agent_runtime/test_step_1_fast_select.py::test_bucket_anchor_is_not_reused_on_the_fast_path。


## 2026-08-14 — 持锚点不创建（实测终案，supersede 新线门条目）

create-on-silence 分支删除：durable+锚点在手时判定梯只剩 强分抢线
（bm25_fast_override）/ 复用（session_fast）/ 锚行消失→无锚重试→create。
`test_live_anchor_never_creates` 钉死。新线来源=无锚首轮 / 强分切既有
线程 / 下一个 full-path turn。实测依据见 config.py.md。

## 2026-08-14 — keyword_ms 口径修正（R2 复核 I1）

`keyword_ms` 一度被挪到整个判定梯之后——把 create/load 的 CRUD 也算了进
去，与 full 路径「BM25 pool load + rank」的列语义（models.py）分岔，fast
行与 full 行不可比。修：只累计两次 probe 的时长（首测 + vanished-anchor
重试），CRUD 不计。新线门常量改引 `FAST_NEW_THREAD_MIN_QUERY_UNITS`
（语言单位，见 config.py.md）。

## 2026-08-14 — auto-review R2 收口：新线门 + 抢线标签（#307 增量 🟡1/🟡2/🟢3-5）

上一轮判序把 create_fast 变成了「持锚点不可达」——fast 模式永不开第二条
narrative（碎片化的镜像失败）。修：`select_fast` 返回 `FastSelectResult`
（narrative/related/suggests_new_thread/top1_raw），锚点在手时若 probe 报
`suggests_new_thread`（top-1 连噪声底都不过——排名单调故锚点必然同样不过
——且 query ≥ `FAST_NEW_THREAD_MIN_QUERY_CHARS`，零重叠的沉默才可信）则
直接 create 开新线；短省略句（"ok"）沉默不可信，照旧复用锚点（防碎片化
不回退，也仍然不是时间窗）。抢线命中（越过 override floor 且落在非锚点
线程）标 `retrieval_method="bm25_fast_override"`，与无锚点命中区分；
audit 行新带 `top1_raw`（写 `gate_top1_raw` 列）——override floor 从此有
线上数据可校准。另：divergence warning 挪到 miss 分支并改述（hit 照常持
久化，只有 miss 不落）；`keyword_ms` 覆盖 vanished-anchor 的第二次搜索；
`_is_user_chat` 结果存局部变量复用。

## 2026-08-14 — auto-review 收口：锚点优先判序 + 去时间窗 + audit（#307 🟡1/🟡2/🟡4/🟡7/🟢8）

判序对齐 full 路径的 continuity-first：durable chat 且 session 带
`current_narrative_id` 时**默认复用该线程**（不看年龄——30 分钟窗
`FAST_REUSE_WINDOW_S` 已删，遵守 2026-05-20「session 永不超时」决定），
BM25 只有越过 `config.FAST_ANCHOR_OVERRIDE_FLOOR`（强分抢线阈值，在
narrative/config.py，经 `select_fast(against_live_anchor=True)` 生效）才
允许把 turn 抢到别的线程；锚点行消失则按噪声 floor 重试再 create。
检索文本改用共享 helper `resolve_retrieval_text`（手抄副本删除——它写进
session.last_query，两路径口径必须同源）。每个决策落一行 RoutingAudit
（`narrative_service.audit_fast`，best-effort，selection_method="fast"，
retrieval_method ∈ bm25_fast/session_fast/bm25_fast_created，带
keyword_ms）。`_is_durable` 改直接属性访问（缺字段要炸响，不许静默滑向
丢历史分支）；durable profile 落在非 user-chat source 时 warning 明示
本轮不持久化。

## 2026-08-14 — durable miss 先复用 session 线程再创建（复核 N1）

小语料 BM25 退化（逐字重复的 query 都可能低于 floor——floor 是噪声滤网
不是强度测试，见 narrative/config.py），miss 即创建会把一场对话打碎成
每 turn 一条 narrative（各带独立 ChatModule instance = 各自的历史，用户
体验为失忆）。修：durable miss 时若 `session.current_narrative_id` 在
`FAST_REUSE_WINDOW_S`（30 分钟）内被触碰过则单次 load 复用
（retrieval_method="session_fast"），行不通才 create_fast。naive 时间戳
按 UTC 处理（与 continuity.py 同规则）。ephemeral（voice）不受影响。
真 service 层锁在 tests/narrative/test_fast_path_service.py（create_fast
持久化 + 小语料 miss 现实性）。

## 2026-08-14 — durable 模式：miss 建 narrative + session 锚定（预审 C1/I4）

签名增可选 `session_service=None`；「结构上不可能写 session」的旧契约改为
**行为契约**（ephemeral profile 零 session 触碰，测试锁死）。
`profile.narrative_persistence == "durable"` 且 `_is_user_chat` 时：
miss → `narrative_service.create_fast`（纯 CRUD，retrieval_method=
"bm25_fast_created"）；hit/created → 镜像 full select() 的四个 session 写
（last_query / current_narrative_id / query_count / last_query_time）并
save——否则下一个非 fast turn 的 continuity 会拿到半新不旧的锚
（step_4 只动 last_response）。voice 行为逐字节不变。

## Why it exists

F28 快速模式下 step_1 的替身：一次 BM25 top-1 直取代替「ContinuityDetector
LLM 判定 + 检索 LLM 层 + 可能的新建」。设计立场是**便宜地保留 narrative**
而非绕开它——命中即注入背景，未命中裸跑，绝不新建。

## Design decisions

- **结构性禁止 session 写入**：签名根本不收 session_service（测试
  `test_signature_has_no_session_service` 钉住）。语音 turn 结束后，普通
  消息的连续性判定行为与语音 turn 从未发生过完全一致。
- **必须保住 ChatModule instance 不变量**：历史装配与 turn 持久化都挂在
  「用户在选中 narrative 下的 ChatModule instance」上，故命中路径复用
  step_1 的 `_ensure_user_chat_instance`；ensure 失败降级（有背景无实例）
  而不是死 turn。
- 检索文本偏好与 full 路径一致：trigger 的 retrieval_anchor 优先于完整
  execution prompt。
- run() 侧同分支跳过 step_1_5（markdown 只喂 instance-decision LLM，而
  skip_module_decision_llm 默认已绕过它）。

## Downstream

`agent_runtime.run()` 在 `turn_profile.narrative_strategy == "bm25_top1"`
时调用；服务侧对应 `NarrativeService.select_fast`。测试：
tests/agent_runtime/test_step_1_fast_select.py + tests/narrative/test_select_fast.py。
