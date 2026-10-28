---
code_file: tests/channel/test_ingress_guard_all_paths.py
stub: false
last_verified: 2026-08-28
---

## 2026-08-28（接线 review）— `build_ingress_guard` 去掉下划线

本文件按**名字**钉住那个工厂（`test_guard_is_built_during_start` 与
`test_managed_guard_reuses_the_channel_tunables`），所以重命名要同步。

改名本身的理由在 [[managed_channel_ingress.py]]：它是跨组件契约，私有名会把
下一个做「清理私有方法」的人引向删掉它，而删掉的结果是托管面失去熔断器。
# test_ingress_guard_all_paths.py — 每条入站路径都要经过守卫

没有单一 chokepoint 可放守卫，所以 seam 是**方法**（`_ingress_admitted`），
而「每条接收路径都调它」这条不变量，类型系统和任何单元测试都拿不住。
与 [[test_trigger_envelope_every_channel.py]] 同一个缺陷类、同一个答案。

**它第一次跑就见了血**：抓出 Telegram / WeChat / Matrix 三个此前没被数进来的
`_process_message` override（都调了 `super()`，所以实际是安全的，但调研阶段
根本没数到它们）。同时暴露出 Matrix 的 `group_silent` 分支在 `super()` 之前
就 return，而那条路仍然跑记忆管线——真漏了一处，因此被补上。

**断言必须防散文**：第一版用 `inspect.getsource` 直接 grep 字符串，结果
两处**注释文字**把断言骗过去了（Lark 里一句「never calls
`super()._process_message`」让「它有没有委托？」答成了是）。所以现在先剥掉
`#` 注释行，再匹配**带左括号**的调用形式。
