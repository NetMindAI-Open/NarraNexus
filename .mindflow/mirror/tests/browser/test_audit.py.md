---
code_file: tests/browser/test_audit.py
last_verified: 2026-09-23
stub: false
---

## 2026-09-23 多页面支持

transport_close 用例关闭浏览器根连接，验证 stopped 审计与进程回收。单个页面关闭
现在只影响该页，不应生成整个会话 stopped。

# 浏览器审计生命周期

验证 service 默认注入持久审计、可信 scope 归属、敏感页面数据排除、异步写入顺序、失败可见、
L2 探测与退出 drain。普通浏览使用空策略，不再构造无效的网站访问允许字段。
