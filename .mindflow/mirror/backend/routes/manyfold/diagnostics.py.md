---
code_file: backend/routes/manyfold/diagnostics.py
stub: false
last_verified: 2026-08-10
---

# diagnostics.py — Manyfold 诊断面:容器健康 + per-agent pull 通道

## 2026-08-10 — per-agent pull 通道(观测性方案 A;mirror 补建)

本文件此前无 mirror(2026-05-25 的旧文件),本次补建。新增观测性
pull 半边(push 半边 = 日志 sink,两者**刻意零共享机制**——观测
通道自身坏掉时另一条还活着):

| 端点 | 数据源 | 回答 |
| --- | --- | --- |
| `…/agents/{id}/diagnostics/ingress` | `channel_trigger_audit`(#267 的托管生命周期行 + files-write 行,按 agent 索引跨渠道查) | "这条消息经历了什么" |
| `…/agents/{id}/diagnostics/events`(摘要)+ `/{event_id}`(全文) | `events` 表 | 列表只给生命周期字段(**SQL 列投影**,重内容列根本不出库);全文只按 id 单取——"DB 只 pull"方针的实现形态 |
| `…/diagnostics/logs/services` / `logs/tail` | `NEXUS_LOG_DIR` 服务日志 | 免进沙盒的 tail(service/date/level/grep) |

## 设计决策

- **日志端点不按 agent 分**(日志是进程级):边界是 **runtime 而非
  用户**——且 gateway token **不是运营方专有秘密**:
  manyfoldFragmentAuth.ts 把它经 URL fragment 交给每个从 Manyfold
  跳转进来的终端用户的浏览器。任何持有者经 files API 本就有 runtime
  内全量 workspace 读写(含多 Manyfold 用户形态),进程级日志不扩大
  既有暴露面。("单用户沙盒"与"运营方凭据"两版论证先后被 review
  证伪,此为与代码一致的第三版);
- **读逻辑复用、鉴权不复用**:tail 借 `admin/logs` 的文件解析/
  seek-tail/level 过滤函数,但鉴权走 gateway token——`/api/admin`
  的 session 鉴权一字未动(方案 A 承诺);
- **脱敏是廉价保险,不是形式保证**:已知凭据键(context_token/
  bot_token/…)的 JSON 形态 + `Bearer <token>` 形态,正则替换后出门;
- **越权即 404**:`events/{id}` 按 `agent_id AND event_id` 查,别人
  的 event 与不存在的 event 不可区分(无存在性侧信道);
- **查询归仓库**(review 第二轮):路由不再手搓 db.get——审计走
  `ChannelTriggerAuditRepository.recent_for_agent`(static,跨 channel
  按 agent),events 走 `EventRepository.diagnostic_summaries/full`
  (投影 + SQL order/limit 都在仓库);顺手把老 `recent()` 的全表拉取
  + Python 排序修成 SQL 下推、`count_by_type` 加投影;
- `since` **真解析**(`_since_floor`):`datetime.fromisoformat`,aware
  → UTC 去偏移;字典序折叠版在带 Z/offset 的边界秒上排序方向反
  ('Z' > '.'),且坏输入曾静默空返——现在 400;
- 时间归一收口到 `utils/db/dialect_time.event_time_str`(公有)——
  此前两个仓库各一份拷贝、路由跨包 import 私有名;
- **先脱敏后截断**(`_redact_clip`):凭据恰跨截断点时闭合引号被切,
  key-pattern 正则失配、token 前缀漏出——顺序即漏洞;
- 上限:行数 50/200、字段 512K **字符**(CJK 下 ≈1.5MB 字节,命名
  `_MAX_FIELD_CHARS` 如实)、tail 2000 行;`grep` 纯子串(无 ReDoS)。

## 上下游

平台侧零依赖(sprite ingress 透传)。生产工单场景的"找门"
(用户 → 沙盒 URL + token)待平台 resolver,见 todo §C 决策 3。
