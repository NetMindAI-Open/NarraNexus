---
code_file: tests/social_network_module/test_memory_write_audit_wiring.py
stub: false
last_verified: 2026-08-25
---
# test_memory_write_audit_wiring.py — 调用方那半的审计接线

[[_entity_updater.py]] 自己那八个 handler 由
[[test_entity_updater_alerts.py]] 覆盖。但**记忆写入并不只在那个文件里**：
[[social_network_module.py]] 自己创建主实体和每个被提及的第三方实体，这些
失败此前只有日志——而且因为 hook 在外层全吞，`agent_runtime` 那层告警永远
看不到它们。

那两个上报调用点上线时**一条测试都没有**，与本 PR 上一轮被打回的
「新胶水零覆盖」是同一个缺口，只是往上挪了一层楼。这个文件补的就是它。

## patch 目标必须是 `social_network_module`，不是 `_entity_updater`

[[social_network_module.py]] 在**模块顶层** import `_report_write_failure`，
函数体里持有的是**绑定后的引用**。所以隔壁测试文件那句
`monkeypatch.setattr(eu, "_report_write_failure", ...)` 对这两处**完全无效**
——断言会对着一个空列表静静通过。

这个坑不写下来，下一个加测试的人会先撞一次「patch 了但 calls 是空的」。

## 两处刻意不用「刚好能过」的写法

- **repo 走显式实参，不绕私有属性**。`_process_mentioned_entities(self, repo, …)`
  本来就收 repo；先塞进 `module._social_repo` 再读回来是多一跳，还给一个声明
  为 `Optional[SocialNetworkRepository]` 的属性赋了假对象。
- **`kwargs["name"]` 而不是 `.get` 链**。真实调用是
  `search_by_name_or_alias(instance_id=…, name=…)`；用
  `kwargs.get("name") or kwargs.get("entity_name") or ""` 的话，关键字一旦
  改名，`seen` 会静静填满 `""` 而 `len(seen) == 2` 照样通过——那条断言就从
  「两个实体都被查了」退化成「被调用了两次」。这正是这个 PR 两轮都在打的
  「绿灯但没测到」。

`operation` 断言的是 [[social_network_module.py]] 的**模块级常量值**，
不是 `inspect.getsource` 的子串匹配——读源码的测试在「调用被删但字面量留在
注释里」时是绿的，在「上报抽进 helper」时是红的，两个方向都错。

## 已知缺口

`create_primary_entity` 只有拼写和常量被钉住，**没有行为测试**——它的调用点
在 `after_turn` 的 `if not entity:` 分支里，要跑起来得把整条
hook 立起来。这一条是本 PR 里唯一没被行为验证的上报路径。

## 钉住了什么

- 两个 `operation` 的拼写（运维几周后 grep 的就是它）
- `entity_id` 必须是**失败的那一个**——`entity_id_candidate` 曾经是 `try` 内
  第一行而 `except` 里要读它，第一轮迭代会从 except 里抛 `NameError`（连带
  丢掉本批剩余实体），后续迭代会把失败记到**上一个**实体头上
- 一个实体失败不能中断整批，且后续实体照样审计
