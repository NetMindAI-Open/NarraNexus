---
code_file: tests/browser/test_policy.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

重写为围绕 full_cdp_access：默认拒绝、非 http 拒绝、精确/通配/继承规则、旧文档（文件能力、grants、
回执、history 标志）可加载且脚本规则不丢、非法 verdict（含旧 `ask`）报错。

# 独立浏览器能力策略

普通网址访问不再进入策略判定。保留上传、下载和高级脚本的独立默认值、origin/通配符匹配、
配置优先级、能力隔离、作用域期限与序列化回归；历史访问数据的退役由 unrestricted 用例覆盖。
