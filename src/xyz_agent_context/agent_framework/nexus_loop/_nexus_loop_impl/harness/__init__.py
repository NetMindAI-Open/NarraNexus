"""
@file_name: __init__.py
@author: Bin.Liang
@date: 2026-07-27
@description: harness 组——NexusAgent 思维模式的结构化载体。

本框架区别于 CC/Codex/Hermes/OpenClaw 的核心设定在这里落地:
**assistant 文本 = 内心独白(self-thinking), 对外表达必须调用工具。**
由此推导出的三个结构差异:
1. 停止语义 = 「不再有动作」而非「不再说话」(stop.py);
2. 文本事件永远走 ui 轨独白呈现, 不构成用户回复(expression.py 分类);
3. 表达工具是独白世界与外部世界的唯一边界, 其参数的流式投影就是
   「用户看到的流式回复」(P3, 接 modeling/arg_stream)。表达工具全部
   由平台 module 赋予并经名单注入——loop 不内建任何发声通道, 它只
   负责思考(Owner 2026-07-27 定稿)。
"""
