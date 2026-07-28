"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: tooling 组——agent 全部能力面的统一承载。

设计原则(Owner 2026-07-27 拍板): **该有的能力一个不缺, 且通用、标准**。
一切能力都收敛为 ToolChannel 生态的成员, 同一分发器、同一策略引擎、
同一事件日志:

- builtin/            内建 function tools(思考原语: 文件/shell/web/上下文自助;
                      对外表达不属于 basic——发声依赖 module 赋予的工具, 经 MCP 进入)
- mcp_channel.py      MCP client(15 模块 86 工具 + 任意外部 MCP server)
- skills_channel.py   技能(agentskills.io 标准, 渐进披露 + agent 自建)
- plugins.py          插件/扩展注册面(pi ExtensionAPI + OpenClaw manifest 合成)
- subagent_channel.py 子代理(P4 实现, 接口 day-1)
- scheduling_channel.py 调度原语: update_plan / sleep(P3/P4 实现, 接口 day-1)
- expansion.py        expand_module 动态能力加载(Owner 核心设定)
- dispatcher.py       统一分发器(channel 注册表 + 并行策略 + policy 检查点)
- policy.py           PolicyEngine(fail-closed, deny 永远赢)

新能力的标准接入方式 = 写一个 ToolChannel 注册进 dispatcher; loop 与
dispatcher 永不为新能力改动(07-26 §6.5 收敛结论)。
"""
