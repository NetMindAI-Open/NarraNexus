---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/runner.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-08 — 改用 `boot_turn_plugins()`（只读 boot）

原因见 [[plugins_boot]] 同日条目：本进程活一回合，不能持有 workers 启动标记。`test_host_entrypoints_boot` 入口表同步改。

## 2026-09-08（复审）— boot 失败也走 wire 协议

`boot_executor_plugins()` 抛错（registry.json 损坏、绑定解析失败）时写 `{"exit": {"ok": false, "error": …}}` 再以 3 退出，
driver 收到的是可读的回合错误而不是 stderr 里的原始 traceback。

## 2026-09-08（本地 E2E 实测）— runner 进程也要 boot 插件平台

runner 是 adapter 按用户 spawn 的独立进程，`assembly.resolve_one(EXPRESSION)` 从本进程注册表取框架自己的座位；此前从不 boot，
注册表只有内核种子树，真机第一次带工具的回合就死在 `unknown slot …nexus_power.expression`（单测都用注入的注册表所以没抓到）。
现在 `main()` 起手 `boot_executor_plugins()`（与 executor_service 同角色 workers），`test_host_entrypoints_boot` 把本文件列进
必 boot 的宿主入口表。

## 2026-08-21 — serve_turn 转发 steering inlet

`serve_turn` 新增 `steering` 关键字参,原样转给 `run_turn_events`;`None` 时行为不变。
类型标 `SteeringInlet | None`,经 `TYPE_CHECKING` 懒导入(不进模块顶层,守 `_prewarm` 冷启动
契约)——这样 transport 调用方能被 Protocol 静态校验,不至于误传裸 `asyncio.Queue`。进程宿主
负责构造并喂这个 inlet——inlet 是活对象,**不跨序列化边界**(见 [[assembly.py]] 同日条目)。
本 PR 只加转发;真正让 stdin 续读 steer 行喂进 inlet 的 transport 是后续改动。

# runner — 独立进程宿主(一个协议两种传输)

Owner 拍板:agent 回合独立进程跑。云端=executor 容器 HTTP;本地=driver spawn 本模块,stdin 一行 TurnRequest JSON、stdout NDJSON({event}/{exit})。行长无上限假设——读端必须手动缓冲(2026-07-08 aiohttp 128KiB 行读上限事故)。SIGTERM/断流→协作取消(配对不变量保住)。每回合事件同时落 <cwd>/.nexus_power/<thread>.ndjson 本地真相文件(C1)。exit 错误带 traceback 尾巴(排障实测必须)。

## 2026-07-29 — NEXUS_POWER_PREWARM

池化契约:PREWARM=1 时在阻塞读 stdin 之前吃掉全部导入(assembly 图 +
litellm),温进程的首 token 不含任何导入成本。

## 2026-08-21 — live steering:stdin 续读

首行请求读取**完全不动**(阻塞、load-bearing)。之后一个 daemon 线程阻塞式续读 stdin,`parse_steer_line`
解析 `{"steer": <provider msg>}` 行,经 `loop.call_soon_threadsafe(queue.put_nowait, msg)` 跨线程喂进
`QueueSteeringInlet`(asyncio.Queue 非线程安全,这是 steering.py 契约里的写入路径)。非 steer 的 run:
driver 写完即 close stdin → 线程立刻 EOF 退出,**零行为变化**。坏行/空行被忽略,绝不掀翻回合。

## 2026-08-21(补)— steer reader 抽成可测函数 + loop 关闭守卫

daemon 线程体抽成模块级 `forward_steer_lines(lines, deliver)`(纯解析+分发,单测覆盖;删它变红,铁律 #4),
线程 wrapper 只供 `sys.stdin` + 跨线程 `deliver`。`_deliver` 用 `call_soon_threadsafe` 且 `try/except
RuntimeError`——turn 已结束、loop 正在关时的迟到 steer 行无处可去,静默丢弃(同"坏行绝不掀翻回合")。
**子进程完整 steer 回合无 fake-model e2e**(跨进程模型墙),靠 dev EC2 手验;in-process e2e 已证 loop 投递半程。

## 2026-08-22(补)— reader 线程 try/except:诚实版

reader 线程体包 try/except(事故教训 #2 线程版)。**准确表述**:runner 进程**从不调** `setup_logging()`
(`main()` 只 `_prewarm`→读 stdin→`serve_turn`,全仓唯一 `logger.remove()` 在 `utils/logging/_setup.py`,不在这
条路径),所以 loguru 保留 import 时自带的默认 **stderr** sink——这行**照样进 stderr**,driver 在回合非 0 退出时
可能把它当 stderr 尾巴带出来。try/except 的**真实收益**是把线程异常从"整段 traceback"收敛成"一行",**不是**
"不进 stderr"。级别用 `logger.warning`(反正会被看到,DEBUG 会假装看不见);`from loguru import logger` 放进
`except` 分支内,别提到模块顶——顶注的"imports 惰性、冷启动按需付费(warm-pool / `_prewarm` 契约)"约束。
真要 stderr 全干净得走 option (b):异常写本回合 NDJSON truth file(`<cwd>/.nexus_power/`),本 PR 未做(该 except
至今无已知触发路径,不值当为它加线程安全的落盘)。

## 2026-08-23(补)— steer_consumed 走独立行,绕过 legacy 白名单

`serve_turn` 里 `TYPE_STEER_CONSUMED` 事件**不**走 `{"event": …}` 流,而写**独立行** `{"steer_consumed": ids}`。
原因:bus 用 `output_mode=legacy_dict`,`LegacyEventAdapter.translate` 有类型白名单、会 drop 未知事件;消费信号是
瞬态控制信号(非 turn 输出),独立行让 driver 直接拦截、不必进 AgentRuntime、也不必给它写一条 legacy 翻译。

## 2026-09-11 — stdout 事件行改走 ndjson_line

`write_line` 原先直接 `json.dumps(..., ensure_ascii=False)` 写 stdout，孤立 surrogate 会在管道编码处抛错；现在复用
[[event_log.py]] 的 `ndjson_line`（已 scrub surrogate），与落盘日志同一出口规则。
