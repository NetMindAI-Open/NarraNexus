---
code_file: frontend/src/lib/netmindAuth/passwordPolicy.ts
last_verified: 2026-08-12
stub: false
---

# passwordPolicy.ts — NetMind 密码策略的客户端单一副本

## 为什么存在

复审三轮 Critical。`PASSWORD_RULES`（8–16 位 + 大写/小写/数字/特殊字符,对齐后端 `password_policy_error`）此前只写在 [[SignUpDialog.tsx]] 里,[[ForgotPasswordCard.tsx]] 一条都没有——于是重置密码时弱密码会被 NetMind 拒,而 `resetPassword` 的**防枚举遮蔽**（[[useNetmindAuth.ts]]）把所有上游拒绝抹成一句「验证码无效」,用户看到「验证码无效」→重取码→同一弱密码→同一句,**永远改不了密码的死循环**。

抽成共享 `PASSWORD_RULES` + `failedPasswordRules(pw)`,两个表单同引用:①消除「同一后端策略抄两处」;②让重置表单**提交前**就挡掉策略违规密码,于是 `resetPassword` 收到的上游拒绝只剩「码错/邮箱不存在」——`resetCodeInvalid` 这句遮蔽才诚实(不再掩盖可修的弱密码)。规则文案键复用 `pages.signup.rule.*`。
