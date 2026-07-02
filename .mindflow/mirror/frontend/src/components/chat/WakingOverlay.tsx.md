---
code_file: frontend/src/components/chat/WakingOverlay.tsx
stub: false
last_verified: 2026-06-17
---

## 为什么存在

冷启动"唤醒"遮罩。云端某用户的 per-user Executor 容器被 idle-cull 回收后,
下一轮要重新拉起它(数秒)。后端在 step_3 检测到冷启动时发一个
`ProgressMessage(step="executor.warming", status="running")`;本组件据此把聊天
卡片虚化 + 显示温柔提示,直到被唤醒的 agent 吐出第一个事件(后端配对发
`executor.warming` 的 `completed`)或本轮结束。

## 设计要点 / 坑

- **配对生命周期**:后端发 running → 醒来后第一个事件前发 completed。前端从
  `currentSteps` 里**取最后一个** `executor.warming` 步看它是否 `running`(步骤是
  累加的,所以要取最新那个)。见 `[[../../../../src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_3_agent_loop.py]]`。
- **`isStreaming` 兜底**:即使 completed 没来(executor 起不来 → 本轮报错结束),
  `isStreaming` 转 false 也会清掉遮罩,不会卡死。
- **作用域**:`absolute inset-0` 挂在聊天卡片的 relative 容器里(MainLayout
  ChatView),只虚化聊天面,App 其余部分仍可交互;不是全屏 modal。
- **文案英文**:遵循"代码内只用英文字符串"(铁律 #1)+ 前端既有约定。若要中文
  温柔文案需先引入 i18n 或 Owner 显式豁免。
- 复用 NM token(`--nm-backdrop`/`--accent-primary`/`--text-*`)+ lucide `Loader2`,
  与现有 modal/spinner 风格一致。
