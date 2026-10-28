---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/prompts/library.py
last_verified: 2026-08-31
stub: false
---

## 2026-08-31 — 「明文送不送达」按轮次分流，正文回 resources

本轮是**行为变更**，不是注释改写：`constitution()` 现在按 `default_reply_tool`
是否为空，给出两套不同的宪法。

### 为什么

`default_reply_tool` 为空 = 平台**收回了全部表达工具声明**，而它这么做的原因
恰恰是「本轮明文自己就是投递物」（平台侧的 `is_plain_text_turn`，今天唯一的
调用方是 team patrol 状态行）。在那种轮次上，08-30 立的规则 1 两半都是假的：
「永不投递」不成立，而「每次调用工具前叙述一句」会把叙述**写进被投递的那行**。

### 收口方式：所有「送达与否」的断言集中到规则 1

第 2、4 条原本各带一句无条件的投递断言（「不调回复工具用户什么都听不到」/
「尾随独白永不投递」），第 3 条还写着「（via a reply tool） 说你做不到」。它们
和分流后的规则 1 正面对撞，**而且第 2 条语气更硬**。

预期的失败形态很难发现：模型采信第 2 条 → 判断「反正明文到不了任何人」→
**什么都不写** → patrol 在 `turn.text` 为空时直接 return → 状态行静默消失，
没有报错、没有日志、测试全绿，只有房间里的人发现它不再出现。

所以不是给第 2、4 条各开一个槽，而是**把投递断言从它们身上摘下来，并入规则 1
那个已有的槽**：规则 1 独占「我的话能不能到人」这个主题，第 2 条只讲「行动
经由工具」，第 4 条只讲「回合何时结束」。一个槽、两个变体，而不是三个槽六个
变体——而且宪法读起来每条只有一个主题。

### 正文回 resources（review 🟡 #18）

第一版把两段宪法正文写成了 `library.py` 的常量，方向与本文件 2026-07-31 那次
「copy-in-resources 归位」正好相反，并违背文件头 Design point 2（「wording
changes never touch code review」）。现已改为两个资源文件，走既有 `_load`：

- `resources/delivery_speaks_by_tool.md`
- `resources/delivery_speaks_by_writing.md`

**`_load` 会 `.strip()`**，而这段正文是夹在规则 1 两段之间的：替换处显式补
`f"\n   {delivery}\n"` —— 前后空行**和首行那 3 个空格缩进**都要补回来（后续
行自带缩进；这 3 空格对齐的是有序列表项）。留在代码里的只有那 9 行**设计注释**，
它解释的是判据不是措辞，放代码里是对的。

### 测试

`test_a_mute_turn_is_not_told_to_narrate_before_each_tool_call` 从「某个子串
不出现」升级为**逐句正面断言**，并且**先归一化空白**——第一版断言写的是
`"never delivered to"`（带 to），而第 4 条是 `is never delivered`（无 to），
**恰好从断言下面漏过去**；归一化之前 `"hears nothing"` 又因为硬换行（`hears\n
   nothing`）匹配不上。两次都是同一类假绿。

`PromptMode.NONE` 的 mute 串必须**同时**带上「其余一切仍只经由工具」——mute
轮仍有非表达类工具（patrol 要读历史），丢掉这半句会让精简面比完整面少一条规则；
同时它受 `test_prompt_assembly_byte_stable_and_mode_faces` 的 200 字符预算约束，
措辞是压着这个上限写的。

## 2026-08-30 — 宪法第 1 条改口：可见的工作叙述（B 路径）

A′（只改展示）在真机上被证伪：宪法说「没人读 plain text」，模型就把该说的话
全写进 thinking，text 位基本空置——展示层无米可炊。TC 裁决转 B：**修宪法，
让模型每次 call 在 text 位说一句。**

`constitution.md` 第 1 条现在说三件事：①plain text 会出现在用户的过程区；
②但它**仍然不是消息**——不投递、不指向任何人（「投递」与「可见」是两回事）；
③**每次调用工具前用一句短话说明要做什么**。

> **2026-08-31 订正**：本条当时写的「第 2、4 条一字未动」已不成立，②③ 两句
> 也不再无条件成立——见下方 08-31 条目。二者都按轮次分流了。

**保住两类模型的推理空间**（铁律 #15：一份文本对所有模型）：第三段按「你有没有
独立推理通道」分流——有的把权衡留在通道里、text 只做叙述；没有的 text 就是
唯一草稿纸，**要多长写多长**。分流依据是**模型自己知道的事实**，不是平台探测，
所以没有按模型定制。

`PromptMode.NONE` 的最小身份串同步改口。

**真机对照（同题同模型，2026-08-30）**：
- claude-haiku（无推理通道）：叙述 208 → **415** 字符，从「开头一句 + 中间
  一句 + 收尾」变成**每次工具调用前都说一句**。
- DeepSeek-V4-Pro（有推理通道）：CoT **1546 → 2824** 字符（推理**没有**被搬进
  text，反而更充分），叙述 233 → 284，混档帧 1 → 0。

## 2026-08-24（补)— wait_timed_out 超时提示

`wait_timed_out(seconds)`:`wait_for_input` 等满 N 秒无消息时,loop WAIT 边界注入的提示。告诉 agent 等待已结束、收尾即可(别以为还在等),并**点名**可"再等一次"的替代但不推它——让真闲的轮收口而非空转。措辞与工具 description 对齐(只在真有回复将至时再等)。

## 2026-08-13（管线审后）— reminder 撤回「ONE call」与 VOICE 多段契约的冲突

「Call ONE reply tool with your complete answer」与 VOICE register 的「long answers
become SEVERAL short speak calls / 预告→答案两连调」直接打架（管线审 I#2）。改为
「连续 reply 调用续写长答案、工具前的进度短句合法；但绝不重复已交付内容」——反重复
半句保留（它是桥等值去重的提示侧对位）。

## 2026-08-13 — reply_reminder 声明默认回复工具（不再平铺列表）

ExpressionContract 的声明序契约（首位=本轮默认回复工具）此前只活在 constitution 的
example slot；动态尾 reminder 把列表平铺，8/13 语音通话实测模型 12/14 轮跟随平铺列表
选了 narra_reply 而非按 per-message 指令用 speak。reminder 现渲染
「THIS TURN's default reply tool is X (other reply tools, only when …)」+
「Call ONE reply tool …never repeat」；消息自带 reply instruction 仍然最高优先。
模板占位从 {{REPLY_TOOLS}} 换为 {{DEFAULT_REPLY_TOOL}}/{{OTHER_REPLY_TOOLS_CLAUSE}}。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

constitution 例子去静态化:`constitution.md` 里的 `send_message_to_user_directly`
硬编码删除,换 `{{DEFAULT_REPLY_TOOL_EXAMPLE}}` 槽位,由 inputs.default_reply_tool
填充(框架 copy 永不写平台工具名;mute 回合无例子可给)。新增
`reply_reminder(reply_tools)`(资源 `reply_reminder.md`):assembly 每步从
ExpressionContract 现值渲染进动态尾部,措辞含「消息自带回复指令优先于默认名单」
——channel 模板(平台侧)与 harness 不再对打。旧 reminder 是 assembly 里的
硬编码英文串,已迁来资源(copy-in-resources 归位)。

## 2026-07-29 — 两份中文占位稿删除，意图收进本文

`resources/compaction.md` 与 `resources/prescriptions/README.md` 是中文设计稿
占位，`library.py` 从未装载过它们——既碰铁律 #22（仓库不留开发记录），日后
真做出来还得是英文 prompt（#1）。占位文件删掉，**设计意图记在这里**（mirror
才是意图的载体，md 占位不是）：

**压缩提示词（做的时候按这个来）**：摘要保留任务状态 / 未完成事项 / 关键文件
与决定，丢过程性噪音；已有 summary 时**增量更新**而不是推倒重来；产出用
"REFERENCE ONLY" 前缀 + 结束 marker 包裹并声明「不要回答摘要中提到的问题」
（防注入）；压缩前先提醒 agent 把重要信息写进长期记忆再压。

**模型行为处方（S2）**：按模型族一个 md（`anthropic.md` thinking 块惯例、
`openai.md` 强制工具使用特化、`weak_models.md` 显式工具调用引导），装配时由
`model_prescription_section` 按 `ModelParams.model` 匹配加载，无匹配返回空串。
铁律 #15 划的线：处方是**通用行为引导**，不是对用户所选模型的限制。

# prompts/library — NexusPowerPrompts 命名空间类

Owner 拍板形态:不可实例化(构造抛 TypeError,无状态是强制)、classmethod 取串、子类覆写=实验包。宪法任何 mode 非空(框架最低身份)。section_order 即契约:改顺序=打穿全网 cache 前缀,必须显式评审。资源经 importlib.resources+lru_cache 装载,缺模板=构建错误不兜底。

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
