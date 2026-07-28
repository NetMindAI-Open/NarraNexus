---
code_file: src/xyz_agent_context/integrations/feedback_client.py
last_verified: 2026-07-10
stub: false
---

# feedback_client.py — 反馈上报客户端（Feedback 机制一期）

## Why it exists

所有部署形态（云端/自部署/本地 run.sh/DMG）把"用户不满/反复失败/主动反馈"
汇到团队同一个接收端（deploy 仓 stacks/feedback-svc）。URL 写死
（agent.narra.nexus/feedback/api/feedback），`NARRANEXUS_FEEDBACK_URL` 供
dev/test 覆盖，`NARRANEXUS_FEEDBACK_DISABLED=1` 是开源合规的杀开关（拍板
决策 B）。

## 设计决策

- **fire-and-forget**：单次尝试、3s 超时、异常全吞（DEBUG 日志）——上报
  链路绝不允许伤害 Agent 主流程或后端请求。
- **本层只强制可强制的部分**：agent_id/user_id 一律 sha256[:16] 哈希后出境、
  summary 截 500 字符、空摘要不发。summary 的内容纪律（不引用户原话/不带
  key）由 prompts/UI 文案治理,代码无法验证——review PR#86 指出后措辞已对齐。
- 两个 env 开关已登记 .env.example / .env.cloud.example（PR#86 Important #1）。
- 上游调用方：[[_basic_info_mcp_tools.py]] submit_feedback 工具（source=agent）、
  [[feedback.py]] web 中继（source=web_ui）；deploy 仓 log_sentry.sh 用同一
  payload 契约（source=log_scan）但不经过本文件。

