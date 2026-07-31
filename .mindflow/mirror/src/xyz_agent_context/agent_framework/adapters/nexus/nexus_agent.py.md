---
code_file: src/xyz_agent_context/agent_framework/adapters/nexus/nexus_agent.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

删除 `_reply_tool_names` 子串猜测(server 名含 "chat" → 猜 reply 工具):
expressive 只读 kwargs(平台经 TurnInput 声明)。改名/换 server 不再静默把
agent 变哑;channel 回复工具(lark_cli 等)也随声明进入表达面。agent_id
同批开始真实传入(旧值恒 "agent")。

## 2026-07-29 — 冷启动路径上钉死 litellm 本地价目表

warm pool 派生 runner 时在**子进程 env** 里 setdefault
`LITELLM_LOCAL_MODEL_COST_MAP=True`。litellm 在 import 时会去 GitHub 拉价目表
(5s 超时后回落到内置副本),这一发请求正好压在我们花 warm pool 换来的冷启动
路径上,还是从加固过的 executor 容器往外打。runner 模块自己也 setdefault 了
一次,但 `-m …runner` 会**先**导入父包——litellm 可能那时已经加载完了,所以
子进程的 environment 才是唯一确定早于一切 import 的落点。
# adapters/nexus/nexus_agent — NexusAgent driver

三件事:遗留签名→TurnRequest(模型配置读同一个 claude_config contextvar——平台 provider 皆 anthropic 协议;bearer_token 补 Authorization 头经 llm_extra 透传)、默认子进程跑 runner(NEXUS_POWER_INPROCESS=1 走进程内,executor/测试用;读行 32MB limit 手动缓冲)、response.done 每路必发恰一次。oauth 凭据显式拒绝(nexus 直驱 API,留在 claude_code)。取消=轮询+SIGTERM 进程组。capabilities={event_log}(声明与实现同批)。


## 2026-07-29 — 温进程池(TTFB 3.4s→1.1s)

_WarmRunnerPool:预 spawn 已完成全部导入(含 litellm 1.8s/215MB)的 runner
闲置待命;acquire 即用即耗(单回合单进程,隔离不变),取用后后台补位;
NEXUS_POWER_POOL_SIZE 定池(默认 1,0 关;每闲置进程 ~350MB RSS,速度换
内存的显式取舍)。driver 构造即预热(首回合与导入重叠)。atexit 收割闲置。
