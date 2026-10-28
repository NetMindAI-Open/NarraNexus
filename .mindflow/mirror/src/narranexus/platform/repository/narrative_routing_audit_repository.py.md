---
code_file: src/narranexus/platform/repository/narrative_routing_audit_repository.py
last_verified: 2026-08-26
stub: false
---

# narrative_routing_audit_repository.py — 路由决策的落库审计（E1）

## 2026-08-26 — `_to_row` 多写合并路由八列

`merged_call` / `anchor_in_menu` 走 `int()`(布尔列),其余六列**原样传**:
`None` 必须活着落成 NULL —— "这一行是两次调用路径决策的"与"合并调用花了
0ms"是两个不同的事实,强转 0 会让前者伪装成后者。与 `continuity_ms` /
`judge_ms` 是同一条纪律,理由也同一条。

## 2026-08-26 — `_to_row` 多写一列 `pool_is_shadow`(切片 0)

标记这一行的池是**只记录、没决策**的(continuity 判 yes 的轮次)。
`WHERE pool_is_shadow = 0 AND is_user_chat = 1` 是决策人群,`= 1` 是
"如果当时问了 gate,它会怎么说"。后台触发的续接轮同样落 0(仪器只记
用户聊天轮),单看这一列区分不出来——人群判别是两列共同。

### 它为什么可以 `int()` 强转,而上面那条 8-20 的约定说不能

两条并不矛盾,差别在**模型侧有没有第三态**:

| 列 | 模型字段 | 有没有"这一轮没跑"这个态 | 写库 |
|---|---|---|---|
| `bypass_score_gate` | `Optional[bool]`,默认 `None` | **有** —— 这一轮压根没走到分数门 | `None` 原样传 |
| `pool_is_shadow` | `bool`,默认 `False` | **没有** —— 要么是影子行,要么不是 | `int()` 强转,永不为 NULL |

所以 8-20 那条"`None` 原样传、不强转 0"约束的是**带第三态的列**;
`pool_is_shadow` 语义上是二值的,把不存在的 NULL 态造出来反而会让读侧多一个
无意义的分支。

### 与 schema_registry 侧"可空 / NULL 与 0 同义"那句的关系

那句描述的是**读侧**:DB 列可空**只为存量行**(prod 上 26,922 行早于这一列,
活表上 `ALTER TABLE ADD COLUMN ... NOT NULL` 无默认值会失败,铁律 #6),
于是过滤查询必须把 NULL 与 0 当同一件事。
**写侧永不产生 NULL,读侧必须容忍 NULL** —— 两句都成立,针对的不是同一侧。
下一轮 review 若再把它读成矛盾,请指回这张表。

## 2026-08-20 — `_to_row` 多写两列(免审决策)

`bypass_score_gate` 按 `continuity_is_continuous` 的同一套写法处理:
`None` 原样传,不强转 0 —— **NULL 的含义是"这一轮没走到分数门"**
(比如 continuity 提前返回、或池是空的),写 0 会让它读起来像"分数门判否",
那是两件不同的事。

`bypass_reason` 是短码字符串,空串表示这一轮没有免审决策可记。

## 2026-08-14 — 四列 per-tier 耗时

`continuity_ms` / `retrieve_ms` / `keyword_ms` / `judge_ms`，原样透传含 NULL。

`None` 表示**这一层没跑**，不是"跑了 0 毫秒"。短路的决策根本不调 judge；把它压成 0
会让"仲裁有多贵"的查询答得远低于真实值——而把成本和决策连起来正是这几列的全部意义。
## 2026-08-07 — 存在性检查失败 ≠ 一条都不存在

`_known_hashes` 失败时返回 `None`（哨兵），不是空集合。把两者混为一谈，写入方会
以为库里什么都没有，于是对整池 ~100 条逐条 insert，每条撞 `text_hash` 唯一索引失
败，再被下一层的 per-row except 以 debug 级别吞掉——净效果是一次 warning + 100 次
注定失败的写，落在 `select()` 的**同步路径**上，每个非连续轮次付一遍。

现在 `_store_snapshots` 见到 `None` 直接整段跳过：少一轮快照，重放时少一条文本；
比每轮 100 次废写便宜得多。日志措辞也改成实际发生的事（"skipping this turn's
snapshot writes"）。

这正是 [[db_backend_sqlite.py]] 那条注记的翻版——advisory except 会把整批静默吞掉。

## 2026-08-07 — review 收口：存在性检查不再把全池正文拉回来

三处，全部来自 PR #256 的 review：

- **`_known_hashes` 取代 `load_snapshots(...).keys()`**。写路径只要「哪些 hash 已
  存在」，而 `load_snapshots` 走 `get_by_ids` = `SELECT *`，于是每个非连续轮次都
  把整池的 `text`（MEDIUMTEXT）拉回来再全部丢掉——而且是在 `select()` 的**同步
  路径**上，每条用户消息都要付，内容还正是同一轮里 `load_pool` 刚从 `narratives`
  读过的那份。100 条池子 + 长摘要 = 单轮几百 KB。为此给 `get_by_ids` 加了 `fields`
  参数（client + 两个后端一起，全项目共享），没有手写 `IN (...)` 原始 SQL。
  `load_snapshots` 保留给重放读路径专用。
- **`recent()` 把 limit / order_by 下推到 SQL**。原来照抄 `service_audit` 的写法：
  全表读回来再在 Python 里排序切片。那张表的 `detail` 是小字段；这张每行带
  `candidates_json`（~100 候选 × id+sha256+分数）且只增不删，几万轮的 agent 会为了
  50 行把几百 MB 拉进内存。趁还没有下游调用点定型。
- **删掉 `snapshot_count()`**。它只服务测试，却是本文件唯一一条手写 SQL——会把这个
  仓库卷进「手写 SQL 必须有 MySQL 覆盖」的强制区，为的还是一条生产永不执行的查询。
  计数搬进测试，生产访问全部走 `insert` / `get` / `get_by_ids` 这些双方言安全的
  client helper。


## 为什么存在

在这之前，narrative 选择的全部证据只进 `ProgressMessage` 和 loguru：
`selection_method`、闸门的分数依据、两个 LLM tier 的判断理由，一个字节都没到
数据库。docker 日志会轮转，grep 只找得到你想到要搜的词（事故教训 #5）。后果是
「路由准确率提升了 X%」没有分母，而 [[continuity.py]] 这一层——一次误判就能靠
`session.current_narrative_id` 连锁锁住若干轮——**完全不留痕迹**，真实误判率至今
未知。

## 非显然的设计约束：只存 ID 和分数是重放不出来的

两条独立原因，缺一条这张表就白建：

1. **BM25 的 IDF 和 avgdl 是在候选集自身上算的**（见 [[retrieval.py|memory 的
   bm25_rank]]）。某个候选的分数依赖池子里**每一个**其他文档，所以存 top-K 重放
   出来是另一组数。2026-08-07 实测：452 条真实本地 query，仅仅移除 8 条 default
   narrative 就让 top-1 翻转 9.7%、闸门决策翻转 5.8%。
2. **被打分的文本本身留不住**。`name + current_summary + description +
   topic_keywords` 被 [[updater.py]] 的异步 LLM 更新几乎每轮全量重写且不留历史，
   所以事后回读 `narratives` 表重建出的池子**是一个从未存在过的池子**——在它上面
   重放会得到自信但错误的数字。

所以审计存**整个池子**，每条指向一份内容寻址的文本快照。去重让代价可接受：相邻
两轮通常只有主 narrative 的摘要变了，100 条候选的池子每轮只新增约 1 行快照。

`tests/narrative/test_routing_audit.py::test_audit_replays_bm25_exactly` 钉住重放
性质——谁把池子裁成 top-K 或只存 ID，它就红。

## 坑

- **`record` 永不抛异常**：观察者不能弄坏被观察者。丢一行审计 ≠ 弄挂用户一轮对话。
- **`load_snapshots` 必须跳过 `None`**：`db.get_by_ids` 为了保持**输入顺序**会给
  查不到的 id **补 `None` 占位**（`db_backend_sqlite.get_by_ids` 的
  `result_map.get(id_val)`），而首次写入时每个 hash 都是 miss。第一版直接
  `r["text_hash"]` 下标，整批被 `record` 的 advisory except 吞掉 → **审计静默不写**
  ——正是这张表最不该有的失败模式。开发时就踩到了，测试钉住。
- 快照 hash 用 sha256 全长而非短摘要：这个 key 是审计行和它要精确复现的文本之间的
  唯一连接，碰撞会**静默污染重放**而不是让它失败。
