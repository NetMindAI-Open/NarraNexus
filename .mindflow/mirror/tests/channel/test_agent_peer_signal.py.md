---
code_file: tests/channel/test_agent_peer_signal.py
stub: false
last_verified: 2026-08-28
---

## 2026-08-28（接线）— 计数守卫改成 AST 判定

原来按**模块内字符串出现次数**比较「构造了几个 ChannelTag」与「填了几次
is_agent_peer」。接线之后 seam 多了一个合法消费方（熔断器也要问同一个问题），
于是同一个模块里「3 次传参、2 个 tag」——**对完全正确的代码判红**。

改成用 `ast` 逐个构造判定。这不只是修误报：字符串计数还会**放过**「一个 tag
填了两次、另一个没填」的模块，总数照样对得上。

哪些算「构造」也从签名推导而不是写死名单：`from_dict` / `parse` 是从已序列化
的 tag 重建，标记本来就在 dict 里，没有调用方能填——按「参数多于一个」把它们
排除，新加的工厂方法会自动进入覆盖。写死名单正是
`build_trigger_extra_data` 那条教训的起点。

改完用两个变异确认没有变弱：漏填一个构造 → 红；用硬编码 `False` 满足关键字
→ 红。

## 2026-08-28（接线 review）— 工厂判据不再只数参数

「参数多于一个才算构造」能正确排除 `from_dict` / `parse` 这类重建型工厂，但会
连带排除将来任何**单字段**工厂（`ChannelTag.slack(sender_id)` 那种形状）——它
构造的 tag 就此不进覆盖面，而漏填的表现只是「报成人类」，不报错。

改成看参数**名**：整体载荷名（`data` / `raw` / `payload` / `tag_str` …）才算
重建。名单显式写出来，加一个重建型工厂是这里的一次有意编辑，而不是「它恰好只
有一个参数」的副作用。

## 2026-08-28（接线 review 二轮）— 判据的取舍写下来

`_WHOLE_PAYLOAD_ARG_NAMES` 把假阴换成了假阳：名单外的重建型工厂
（`from_json(s)` / `of(blob)`）会被当成构造器，测试要求它填标记 → **响亮地红**，
附带 docstring 解释。旧判据（只数参数个数）失效方向相反——将来的单**字段**工厂
会静默退出覆盖面，而漏填的表现只是「报成人类」，不报错。

所以加重建型工厂时：要么把参数命名成名单里的词，要么把新名字加进名单。注释里
补了后半句——原来只说了「加重建器是一次有意编辑」。
# test_agent_peer_signal.py — 一个定义、处处填上、到达模型

钉三件事，缺一个这个信号就没有价值：

1. **seam 答得出**（每渠道一个定义，默认「人」是安全方向）
2. **每个 ChannelTag 构造点都填**
3. **tag 真的渲染出来**，模型能看见

**第 2 条是最脆的**，所以那条守卫是数量比对：**构造点数量必须等于填充数量**。
漏填不会报错，只会静静地报「这是人」——与 `build_trigger_extra_data` 当年
那个缺陷类完全同形（四处手抄、新键只加了一处，导致 Lark p2p 和
NarraMessenger DM 悄悄失去 DM 兜底）。

守卫的扫描面**从 `CHANNEL_TRIGGER_MAP` 反推**，不是写死模块列表——第一版
写死三个模块，因此看不见第四个构造点（`backend/routes/manyfold/sync.py`），
而那个正是**模型真正读到的** tag。写死列表的守卫给的是「CI 会拦我」的错觉，
新渠道会直接走过去。另有一条对账：注册表数量必须等于已注册类名数量，否则
「渠道没装 = 守卫不检查」。

托管那条是**行为断言**而不是源码字符串比对，而且**调真实的
`ManagedChannelIngress.before_run`**：跑
`build_inbound_run_context` → `before_run` → `retag_managed_input`，断言最终
送进模型的字符串里含标记。

这里踩过两个坑，都值得记：

1. **源码比对看不见「盖章顺序错了」**——盖章确实在，只是发生在渲染之后，
   所以第一版是绿的，漏掉了一个 Critical。
2. 改成行为断言之后，第二版**手抄了 `before_run` 里那三行盖章逻辑**而不是
   调它。于是把生产代码里整段盖章删掉，全套测试依然全绿——测试自己把标记写
   进了字典。**测试实现了一遍被测行为 = 什么都没测。** 现在替掉
   NarraMessenger 的 fail-closed 授权门（`managed_before_run`）再调真的
   `before_run`；「不好 mock 就退回手抄」正是这个坑本身。

计数守卫按**模块种类**分两条判据（都从注册表推导，不是字面白名单）：
trigger 模块必须传 seam 的返回值（否则下一个人可以用硬编码 `False` 把 CI
哄绿，而硬编码 False 正是这个守卫要防的失败）；托管构造点允许 `False`
（那时 trigger 还没跑），但必须同时存在重渲——少了重渲，这个 False 就不再
合法。`_code()` 用 `ast` 把 docstring 也剥掉，避免注释/示例进计数。

做过六次变异验证：拿掉 Lark 填充 → 计数守卫红；把 Lark 的 seam 调用换成硬
编码 `False` → 红；把 `retag` 变 no-op → 托管行为断言红；把 `retag` 改名
（托管那处 False 失去合法性）→ 红；**删掉生产代码里整段盖章 → 红**（第 2 个
坑的回归防线）；**删掉 route 里那行重渲调用 → 红**（见下）。

## 第 3 个坑：用截断改这个文件，静默删掉 7 条测试

第 3 轮 review 抓到的：上一次改这个文件用的是「在标记串处截断」，把标记点
之后的内容整段砍掉，误删 7 条——其中包括
`test_the_prompt_clause_names_the_marker`（**这个 PR 自己写下的失败判据**：
prompt 字面量与 `AGENT_PEER_MARKER` 之间唯一的链接）和「不会拼出第二行 tag」
那条守卫。

而当时看到「15 passed」就过了。**绿灯不等于完整**——存活的那些照样通过，
数量掉了 5 条没人发现。

改这个文件之后**必须对比测试名集合**，不是只看通过数：

```
comm -23 <(git show HEAD:<file> | grep -oE "^(async )?def test_[a-z_]+" | sort) \
         <(grep -oE "^(async )?def test_[a-z_]+" <file> | sort)
```

## route 那一环在 tests/backend 里

「route 真的调了 `retag_managed_input`」不在本文件——本文件所有 retag 断言都
是直接调那个函数，删掉 route 里的调用照样全绿。那一环由
[[test_manyfold_im_ingress.py]] 的两条真实端点测试盯着
（ASGITransport + `compat_app`，断言标记出现在 `drive` 的 `input_content` 里）。

最后一条钉的是 **prompt 文案与标记必须一致**——协议里点名一个 tag 从不渲染
的标记，等于给模型留一条走不到的分支。
