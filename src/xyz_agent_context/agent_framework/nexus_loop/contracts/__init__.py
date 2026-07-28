"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: L0 契约层——纯数据类型 + Protocol 接口,是整个 nexus_loop 包的最底层。

分层导入铁则(单向依赖流,自下而上):
    contracts(本层, 只依赖标准库)
      <- _nexus_loop_impl/ 各组件组(只 import contracts, 组与组之间禁止互相 import)
      <- _nexus_loop_impl/loop.py(只 import contracts, 组件经 LoopAssembly 注入)
      <- assembly.py(import 各组件组, 唯一的默认装配点)
      <- driver.py(import assembly + 平台缝 agent_framework.loop.*)

本层禁止 import 本包任何其他模块, 也禁止 import 平台代码(module/ narrative/
agent_runtime/ backend/)。两个组件需要共享的类型, 唯一的家就是这里。
"""
