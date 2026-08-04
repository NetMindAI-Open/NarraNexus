---
code_file: src/xyz_agent_context/module/managed_channel_ingress.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — silent_ingest 只调缝(review)

不再触碰 trigger 私有方法;契约知识(extra 字典 → Attachment 对象)留
协调器,编排知识归 trigger 的 `managed_silent_ingest`。
# managed_channel_ingress.py — 托管模式的 trigger 执行体宿主

## 为什么存在

Manyfold 托管模式下平台持有连接与清洗流水线,IM 消息以 chat turn 形式
经 `/v1/chat/completions` 转发进来;但原生 channel trigger 的
per-message **业务钩子**(wechat 首聊认主、narramessenger authorize
门、inbox 写入、错误兜底直发)都长在平台接管走的那条收消息路径上,
prompt 级映射够不着。本文件是这些钩子在托管模式下的宿主:按
CHANNEL_TRIGGER_MAP 惰性构造 trigger 实例(**绝不调 start()**——无订阅
循环、无连接),在 openai_compat 的 run 前后路由
`managed_before_run` / `managed_after_run`。

## 上下游

上游:`backend/routes/openai_compat.py`(gate 在 BackgroundRun 构造前,
deny 直接回执;after_run 在流收尾 finally 里 fire-and-forget + done
callback)。下游:`channel_trigger_base` 的 managed 缝(默认实现 +
wechat/matrix 覆写)。它是 `run_channel_triggers` 的对等物(trigger
注册表上的协调器),不是 Module——铁律 #3 不受影响。

## 2026-08-03(补2) — `silent_ingest`:非 @ 群消息的记忆摄取

`is_mention=false` + `chat_type=group` 的托管 turn 不跑回复 run(会闯进
群闲聊),改调 trigger 的原生
`_build_and_run_agent_silent_batch`(单条成批):narrative 路由 + 记忆
写入照跑、LLM step 跳过、绝不向房间发任何东西;openai_compat 以回执
completion 应答。never-raise,失败降级为 dropped 回执。合批(平台侧
collect / 我方批量语义)按 spec Q8 留待实现期与平台共定——单条成批
先保证语义正确。

## 2026-08-03(补) — `convert_attachments`:平台落盘附件并轨原生协议

平台 ingest 已把附件写进 workspace(`chat-attachments/...`)并在契约里
传 `{name,mime,size,path}`;但原生 marker 经 upload store 的 per-day
索引解析路径,平台落点不在索引里 → 必须把每个文件**重新经
`persist_attachment_bytes` 入库**(= 原生 fetch_attachments,"下载"换
成本地读),拿到 file_id/marker/STT。转换后写
`extra_data["attachments"]`,原始键必被消费(未转换的平台字典绝不能
流进 marker 管线)。全程 never-raise:坏 ref 单文件降级 text-only。
路径逃逸守卫与 files.py 的 `_safe_resolve` 同语义。

## 关键决策 / Gotcha

- **失败语义按钩子性质分叉**:副作用渠道 fail-open(构造失败/钩子异常
  → 放行,下游自然暴露 no_credential),narramessenger fail-closed
  (它的钩子就是授权门;类缺失/异常 → deny)。
- `synthesize_managed_message` 只重建业务钩子需要的最小 ParsedMessage;
  wechat 回复路由读 `raw["context_token"]`(wire 上是 reply_token)。
- 单例 + per-channel 实例缓存;构造失败按渠道隔离(与
  run_channel_triggers 同款防御姿态)。
