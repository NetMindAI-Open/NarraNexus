"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: NexusAgentLoop——自研 agent-loop 框架(独立、可任意配置)。

对外只有一个使用面: assembly.py 的 TurnRequest / build_assembly /
run_turn_events + contracts/ 的类型。消费方是 adapters/nexus/nexus_agent.py
(AgentLoopDriver 薄接口, 与 adapters/claude 同构); 平台其余代码不直接
import 本包。

框架身份(与四家开源 harness 的根本差异): assistant 文本 = 内心独白,
对外表达必须调用 expressive 工具——思维模式见 _nexus_loop_impl/harness/,
prompt 面见 _nexus_loop_impl/prompts/resources/constitution.md。
本包与 Nexus 平台/modules 解耦: 平台信息一律以 TurnRequest 数据传入。

不 re-export 实现符号(铁律 #23); 本 __init__ 保持空导出。
"""
