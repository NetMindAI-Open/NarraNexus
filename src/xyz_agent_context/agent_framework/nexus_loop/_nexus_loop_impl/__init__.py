"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: nexus_loop 私有实现层(铁律 #23: 私有实现进 _*_impl/, 永不 re-export)。

组内分五个高内聚小组, 组与组之间禁止互相 import(共享类型一律上提 contracts/):
- harness/   思维模式层: 独白/表达契约、停止评判、插话、hook
- prompts/   Prompt 集中存放(纯函数 section 装配; 工具 description 除外)
- modeling/  模型层: litellm 包装、provider 方言表、cache 策略、参数流抽取
- tooling/   工具层: 分发器、策略引擎、内建工具、MCP 通道
- session/   记账层: 回合账本、事件日志、错误分类
加 loop.py(相位推进器)与 event_adapter.py(遗留契约唯一翻译点)。
"""
