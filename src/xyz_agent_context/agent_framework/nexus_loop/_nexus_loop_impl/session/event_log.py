"""
@file_name: event_log.py
@author: Bin.Liang
@date: 2026-07-27
@description: EventLogWriter 实现——两轨日志的落地出口(C1 约束执行者)。

v1 = StreamingEventLogWriter: 事件随流 NDJSON 回传控制面(executor 容器
无 DB、无平台密钥——铁律 #20 stateless worker)。
**落库从 P1 提前进 v1**(Owner 2026-07-27: 日志一定要记录好): 控制面
消费回传流写 nexus_events 表(双方言, schema_registry 注册, 平台侧
配套改造), 落库后即是持久真相, 容器死 = session 不死; compaction 事件
是 narrative 联动的数据源(记忆服务消费它沉淀长期记忆)。长期形态对齐
Codex reconcile 模式: 流式日志为真相源、DB 做索引投影、不一致时以
日志重建。loop 侧接口不因落库时点改变。
"""

from xyz_agent_context.agent_framework.nexus_loop.contracts.events import LoopEvent


class StreamingEventLogWriter:
    """v1: 把事件写进回传流的旁路缓冲(与 driver 的 yield 通道共源)。"""

    def __init__(self, thread_id: str) -> None:
        """thread_id: (thread_id, seq) 幂等键的前半。"""
        ...

    async def append(self, event: LoopEvent) -> None:
        """append-only 写入; 本方法必须无阻塞快路径(日志是路过不是分叉,
        不能拖慢事件流)——缓冲背压策略实现期定, 但接口不变。"""
        ...

    async def flush(self) -> None:
        """回合收尾/错误路径强制排空缓冲(response.done 前必须调用——
        计费事件不许滞留缓冲)。"""
        ...


class NullEventLogWriter:
    """测试/降级用: 丢弃全部事件(显式选择, 不是默认)。"""

    async def append(self, event: LoopEvent) -> None:
        """no-op。"""
        ...

    async def flush(self) -> None:
        """no-op。"""
        ...
