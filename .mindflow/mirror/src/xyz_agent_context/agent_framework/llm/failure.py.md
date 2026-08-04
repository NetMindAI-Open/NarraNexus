---
code_file: src/xyz_agent_context/agent_framework/llm/failure.py
last_verified: 2026-08-01
stub: false
---

## 2026-07-30 — 免费额度用完拆成自己的 reason + `OUT_OF_CREDIT_REASONS`

`free_tier_exhausted` 从 `insufficient_balance` 里拆出来。两者对用户长得一样（都是
「没钱了」），**补救措施却相反**：免费额度的钱包用户无法充值（`/api/admin/quota/topup`
要 `role=staff`），key 也从未在用户手里（网关只对服务器显示一次），所以通用文案里那两条
出路对它**物理上都做不到**。

**判据是 marker，不是卡片来源**：只有我们自己的 LiteLLM 网关会执行 per-user 预算，而且它
在报文里明说（那四个 marker 原本就分组在一起、注释写着「free-tier wallet spent」）。
读报文而不是从配置推断，是为了让**上游故障保持诚实** —— 共享上游干了会返回 NetMind 的
`balance not enough`，仍归 `insufficient_balance`，因此绝不会拿我们自己的故障去催用户付费。
判定顺序放在 `insufficient_balance` **之前**（最具体优先，与本表既有约定一致）。

**类型表命中后仍要看报文**（2026-08-01 补）：`classify_self_serviceable` 原本一旦命中
`_SELF_SERVICEABLE_TYPES` 就立即返回、完全不读 message。而 `billing_error` 是 SDK 折叠出的
枚举，语义只到「没钱了」为止 —— 报文里明写 `Budget has been exceeded` 也会被判成
`insufficient_balance`，正好把免费额度用户推回那对做不到的建议。现在的规则是：**类型表命中
给出「类别」，报文决定「是哪一种」** —— 命中值属于 `OUT_OF_CREDIT_REASONS` 时再跑一遍
free-tier marker 做细化。刻意限定在这个集合内：context-window 错误不会因为正文里出现
「budget」就变成预算错误。只有 raw-exception 路径能走到（inline 路径 error_type 塌成
`unknown`），所以此前一直没暴露。

文案里的套餐**具名 Nexus Pro**（2026-08-01，随 #222 改名），与既有「升级 Nexus Pro」用词
一致 —— 泛称「a plan」在一个已经有确定产品名的界面里只会让人多问一句。

文案分两处、刻意不同：**聊天里**（`chat.error.action.free_tier_exhausted`）走短版，因为
[[MessageBubble]] 就在旁边给了两个按钮；**本文件这条**保留「在哪操作」的指引 —— 它还会
到达没有按钮的地方（暂停任务的失败原因、后台 LLM 告警），必须能独立成话。

已接受的误报：用户自己的 provider 若本身是带 per-key 预算的 LiteLLM 代理
（`custom_openai` / `custom_anthropic` 接受任意 base_url），会产生同样报文而被标成免费额度
用完。代价是建议不当，不是流程中断。彻底消除需要 per-slot 卡片来源 ——
`get_provider_source()` 现在是粗粒度的（`providers/resolver.py` 对所有用户卡硬编码
`"user"`），单独跟进。

### `OUT_OF_CREDIT_REASONS` 存在的理由（踩过）

拆分第一版**同时破了两个事故防线**，因为它们各自与那个**单个**旧常量做相等比较：

- [[circuit_breaker]] 的 `_is_out_of_credit` → 不再归 QUOTA → 断路器不再暂停 →
  **额度用尽的用户无限重试**
- [[job_trigger]] 的 `_EDGE_ONLY_RESUME_REASONS` → 少了它就被交回时间兜底盲探 →
  **每个周期重新拉起暂停的任务，正是 390 次重试那场风暴**

两处都被既有测试抓到（它们恰好用了网关预算那条报文）。所以现在有一个规范集合，
**问「是不是没钱了」的消费者必须做成员判定、不许与单个成员比较** —— 这是让下一个新增
reason 不再悄悄掉出这两道防线的机制，并有测试断言这层关系本身。

## 2026-07-30 — 第四个 self-serviceable reason：`invalid_credentials`（凭据被拒 ≠ 登录过期）

新增 `SELF_SERVICEABLE_REASON_INVALID_CREDENTIALS` + `_INVALID_CREDENTIALS_MARKERS`
+ 对应文案，排在 marker 表**最后**（既有分类一律保持原 reason，例如同时提到
403 和余额耗尽的报文仍归 `insufficient_balance`——那个补救措施才是有用的那个）。

**为什么不复用已有的两条 auth 路径**：语义是「补救措施」而不是「哪个 HTTP 码」。
`response_processor._is_auth_failure` 管的是 **OAuth/CLI 登录死了** → 文案叫用户
`claude setup-token` 重新登录；这里管的是 **provider 拒绝了一把 API key** → 文案
必须叫用户去 Settings → Providers 重贴/换 key。把后者塞进前者，等于让一个从来
没登录过的 BYOK 用户去找一个不存在的登录。

触发事件（2026-07-29 Jiaxi 报障）：用户自己的 NetMind key 返回
`403 {"error":{"message":"Invalid api token"}}`，**三个分类器全部漏掉**——auth 短语
表里有 `401` 没有 `403`，有 `invalid api key` 而报文写的是 `api token`；
`classify_self_serviceable` 也没有对应 reason。于是这一轮被判 `recoverable`，
helper-LLM 兜底编了一条像样的回复，用户看到 agent 承诺干活却什么都没发生。
注意 [[circuit_breaker.py]] 早就通过 `is_credential_error` 的 `" 403"/"(403"` +
`forbidden` 把 403 归到 AUTH 了——漏的只有**面向用户那条消息**的路径。

marker 收窄纪律（和 `"402 payment"` 同源，但这次是被测试抓住的）：一开始写成
`("403", "token")` 的 AND 组，被
`"generated 403 tokens before the stream ended"` 命中——token 计数里天然有 403。
现在要求 403 必须与凭据/权限词共现（`forbidden` / `invalid token` / `invalid api`
/ `credential` / `permission denied`）。这里假阳性代价是双份的：既把这一轮误判
fatal，又让熔断器跳过一次真实故障。

## 2026-07-28 — 认识「网关钱包花光」的样子

`_INSUFFICIENT_BALANCE_MARKERS` 增加了 LiteLLM 预算超限的几种措辞
（`budget has been exceeded` / `exceeded budget` / `crossed spend within
budget` / `exceededbudget`）。

这不是随手加几个关键字：免费额度耗尽从「解析期门禁」变成「调用期错误」之后，
**这里就是它唯一的入口**。后台 job 靠 `classify_self_serviceable` 这一层
（job_trigger 的第 2 层）判定 `paused_no_quota`，marker 漏了就是重试风暴。

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

NetMind 特化的引导改放在 [[resolver]] 的 `QuotaExceededError`：那条路径
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

# llm/failure.py — LLM 失败的统一分类 + 密钥脱敏

## 2026-07-15 — 收紧 self-serviceable markers（PR #110 review）

marker 支持两种形态:纯子串,或 AND-组（`tuple[str,...]`，全部命中才算）。
收紧两处过宽子串——`402` → `402 payment`（token 计数里常有裸 402）;
`does not exist` → `("model", "does not exist")`（文件/会话也会"not exist"，
必须与 `model` 共现）。动机:自助类误判现在**代价更高**——不仅把该轮标
fatal，还让熔断器早退跳过（见 [[loop/circuit_breaker.py]]），可能掩盖真正
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
  认出真相——这正是配合 `adapters.claude.sdk._inline_assistant_error_event`
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
