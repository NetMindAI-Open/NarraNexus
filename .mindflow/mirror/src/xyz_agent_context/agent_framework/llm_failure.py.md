---
code_file: src/xyz_agent_context/agent_framework/llm_failure.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — 新增并列的 executor-infra 分类器（与 self-serviceable 解耦）

新增 `classify_executor_infra_failure(error_type, error_message) -> reason|None`
+ 文案 `EXECUTOR_INFRA_USER_MESSAGE` / `executor_infra_user_message`，reason 常量
`EXECUTOR_INFRA_REASON_OOM="executor_oom"` / `_UNREACHABLE="executor_unreachable"`。

**为什么单开一个分类器而不是塞进 `classify_self_serviceable`**（铁律 #8）：两类
语义正交。self-serviceable = 用户改配置能修（去 Settings）；executor-infra =
**平台侧**失败，用户改设置修不了，正确引导是"重试 / 拆小任务"。混进去会污染
后者语义，也会让前端"Action needed → Settings"徽章对一个 OOM 说错话。

两条识别通道，刻意不同：
- **OOM**：只有子进程 returncode 折进错误串这一个信号——`"exit code -9"`
  (SIGKILL/OOM) / `"exit code -6"` (SIGABRT)，子串匹配。**正的** exit code（agent
  跑的某个工具失败）绝不匹配。
- **不可达**：executor 边界抛的**类型化**异常
  `ExecutorUnreachableError`（见 [[executor_errors.py]]），按异常**类名**匹配，
  不做脆弱文本匹配——这样才把用户 LLM-provider 的连接抖动（走 response.error /
  瞬时，别处理）与 executor 基础设施失败区分开。

下游：`step_3._fallback_skip_decision` 现返回三元组 `(kind, reason,
target_error_type)`，infra 命中 → `error_type=infra_transient`
（[[runtime_message.py]]），同样 **skip helper-LLM 兜底**（不被编造回复掩盖），
并写审计事件（[[executor_audit.py]] `oom_killed` / `executor_unreachable`）。
文案 provider 中立（延续下方 2026-07-20 约束），只信息告知不 force-stop（铁律 #14/#15）。

**已知边界（PR #133 review Minor）**：`_OOM_RETURNCODE_MARKERS` 对整段错误文本做
子串匹配；新加的 `"exit code -6"` 比 `-9` 更可能出现在 agent 自己跑的子进程报错文本
里。当前只作用于**逃出 agent loop 的异常**（子进程报错通常被 agent 自己消化、不冒成
loop 级异常），风险低。若将来 OOM 识别要更严，应改成解析结构化 returncode 而非子串。

## 2026-07-20 — 本文案保持 provider 中立（一度加过 NetMind 特化，已回退）

`SELF_SERVICEABLE_USER_MESSAGE[INSUFFICIENT_BALANCE]` 曾短暂加上「订阅
NetMind.AI 套餐」，随 review 意见回退。

原因：**这条文案是 provider 无关的通用文案** —— DeepSeek 402、OpenAI
`insufficient_quota`、Anthropic credit-balance 都会命中它（测试均有覆盖）。对一个
DeepSeek 余额耗尽的用户推荐"订阅 NetMind.AI"，是无效噪音。

NetMind 特化的引导改放在 [[provider_resolver]] 的 `QuotaExceededError`：那条路径
是免费额度专属，按构造就是云端 + NetMind 语境，在那里点名 NetMind 永远成立。

**若将来要在这里按 provider 分别渲染**，需要把 provider 类型透传进
`self_serviceable_user_message`（调用点两处：`response_processor` 与
`step_3_agent_loop`），那是结构改动而非文案改动，别顺手做。

## 2026-07-16 — 补 Anthropic 余额 marker + 余额文案指向 Settings→Providers

两处:
1. `_INSUFFICIENT_BALANCE_MARKERS` 补两个字面 marker:`"credit balance is too low"`(Anthropic)
   与 `"balance not enough"`(NetMind 400 的字面串——注意与既有 `"not enough balance"` 词序不同,
   之前漏检)。两者之前都不被任一 marker 命中 → Anthropic/NetMind-400 余额错既不被实时层当自助类、
   也不被 Job 层暂停。additive,把上游事故的字面串(`Insufficient Balance` 402 / `balance not
   enough` 400)钉死。
2. `SELF_SERVICEABLE_USER_MESSAGE[INSUFFICIENT_BALANCE]` 文案增强:指引去 Settings → Providers
   (现在显示每把 key 属于哪个 NetMind 账户),提醒充值约几分钟生效。

配合 `classify_self_serviceable` 被 Job 层复用(job_trigger)以暂停余额死掉的后台 job。

# llm_failure.py — LLM 失败的统一分类 + 密钥脱敏

## 2026-07-15 — 收紧 self-serviceable markers（PR #110 review）

marker 支持两种形态:纯子串,或 AND-组（`tuple[str,...]`，全部命中才算）。
收紧两处过宽子串——`402` → `402 payment`（token 计数里常有裸 402）;
`does not exist` → `("model", "does not exist")`（文件/会话也会"not exist"，
必须与 `model` 共现）。动机:自助类误判现在**代价更高**——不仅把该轮标
fatal，还让熔断器早退跳过（见 [[agent_circuit_breaker.py]]），可能掩盖真正
需要熔断保护的 provider 故障。matcher 抽成 `_marker_hit(marker, hay)`。

## 为什么存在

每条后台 LLM 路径都要回答同样两个问题：**"这是不是凭据/鉴权失败？"** 和
**"怎么把这个错误给用户看又不泄露他的 key？"** 在此文件之前，这套逻辑只存在于
`message_bus_trigger`，其余路径（narrative updater、Step-5 entity/memory hooks）
直接把 401 静默吞掉。2026-07 事故——平台 OpenAI key 过期，长记忆退化约两周无告警——
的根因之一就是这套判断没有被复用。本文件把它收敛成单一真源。

- `is_credential_error(err)`：对**原始**错误串做粗粒度子串匹配（`CREDENTIAL_ERROR_MARKERS`）。
  只用于决定 owner 提示文案 + 审计分类，绝不改变重试/投递行为。接受 str 或异常。
- `redact_secrets(text, max_len)`：给**要展示**的错误串脱敏（`sk-...` / `key=...` /
  `Bearer ...`）并截断。不是安全边界，只覆盖 SDK 常见回显形态。

分类读原文、脱敏产出展示文——两者刻意分开：分类必须看未脱敏的文本。

### 2026-07-14 · 确定性自助类失败分类器（"黑盒" P1）

新增第三类判断：`classify_self_serviceable(error_type, error_message) ->
reason|None`。区别于 auth（凭据失效，走 re-login）和瞬时抖动（重试即可），
这类是**同配置每轮必复现、只能由用户改配置**的确定性失败——context window
太小 / 余额不足 / 模型 ID 无效。之所以放这里：它和 `is_credential_error`
同源（都是读原始错误串分类），且需要被 `response_processor`（inline 错误路径）
和 `step_3_agent_loop`（raw-exception 路径）**共用而不产生循环导入**（同
`AUTH_EXPIRED_ERROR_TYPE` 放 schema 层的理由）。

- **双通道判断**：先精确匹配 error TYPE（异常类名 `ContextWindowExceededError`
  / SDK 枚举 `billing_error`），再对 `type + "\n" + message` 做子串匹配
  （`context window` / `must be <=` / `insufficient balance` / `does not exist`
  等）。这样即使 SDK 把 type 压成 `unknown`，也能从折进 message 的 stderr 里
  认出真相——这正是配合 `xyz_claude_agent_sdk._inline_assistant_error_event`
  把 stderr 折进 error_message 后能生效的前提。
- **正向识别**：只认已知形态，残余"我们自己的 bug / 无法归因"桶保持不动。
- `self_serviceable_user_message(reason, raw_detail)`：组合每类的**可操作**
  引导文案 + 脱敏后的 provider 原文（保留 token 数字），供两条错误路径共用。
  文案只是信息告知（铁律 #15），不 force-stop、不判定模型、不替用户换模型。

下游把这类错误标成 `severity=fatal` + `error_type=config_actionable`，
`step_3` 据此 **skip 掉 helper-LLM 兜底**（否则兜底会用一条正常样子的回复
掩盖掉可修复的真相——就是这条 P1 的根因）。

## 下游

- `message_bus_trigger._classify_error` / `_redact_error_for_owner` 委托到这里。
- `services/background_llm_alerts` 用它给后台失败分类 + 脱敏。
- narrative updater / Step-5 hooks 用 `is_credential_error` 判断是否要告警。
