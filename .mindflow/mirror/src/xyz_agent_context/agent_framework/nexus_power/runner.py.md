---
code_file: src/xyz_agent_context/agent_framework/nexus_power/runner.py
last_verified: 2026-07-29
stub: false
---
# runner — 独立进程宿主(一个协议两种传输)

Owner 拍板:agent 回合独立进程跑。云端=executor 容器 HTTP;本地=driver spawn 本模块,stdin 一行 TurnRequest JSON、stdout NDJSON({event}/{exit})。行长无上限假设——读端必须手动缓冲(2026-07-08 aiohttp 128KiB 行读上限事故)。SIGTERM/断流→协作取消(配对不变量保住)。每回合事件同时落 <cwd>/.nexus_power/<thread>.ndjson 本地真相文件(C1)。exit 错误带 traceback 尾巴(排障实测必须)。

## 2026-07-29 — NEXUS_POWER_PREWARM

池化契约:PREWARM=1 时在阻塞读 stdin 之前吃掉全部导入(assembly 图 +
litellm),温进程的首 token 不含任何导入成本。
