---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/builtin/shell.py
last_verified: 2026-07-29
stub: false
---
# tooling/builtin/shell — bash 执行

自建进程组+超时全组 SIGKILL(孤儿进程事故类)、输出帽、exit code 附注。bash_background/process 是 P3 座位(schema 诚实:未启用不注册)。

## 2026-07-29 — 环境变量是白名单，不是继承

agent 的 shell 拿到的 env 由 `_ENV_ALLOWLIST` + `ctx.extra_env` 拼出来，
**不是** `{**os.environ}`。原来那种写法把宿主进程手里的一切都递给了模型——
provider key、DB 密码、master secret——一条 `env` 全读走（铁律 #20 划的
scoped creds 线，2026-07-29 review 抓到）。

白名单里的项是「让 shell 成为一个能用的 shell」所需，不是「让它有特权」所需：
没有 PATH 什么都解析不了，没有 HOME git 和各种 CLI 行为异常，locale 缺了
非 ASCII 输出会乱。本回合自己的 scoped 值走 `ctx.extra_env`，那是**应该**
在的。将来 agent 真需要哪个变量，一次加一个、明确地加。

