---
code_file: frontend/src/components/jobs/JobScheduleEditDialog.tsx
last_verified: 2026-07-30
stub: false
---

# JobScheduleEditDialog.tsx — 编辑定时任务「执行时间」的弹窗

## 为什么存在

Jobs 面板此前只有暂停/恢复/取消,无法调整一个已建任务的执行时间(只能取消重建)。
这个弹窗补上「修改调度规则」的入口,由 `JobExpandedDetail` 的「编辑时间」按钮触发、
`JobsPanel` 持有开关状态,提交后调 `api.updateJobSchedule` →
`PUT /api/dashboard/jobs/{id}/schedule`。

## 设计要点

- **表单模式自适应**:按 `job.job_type` + 现有 `trigger_config` 决定初始模式——
  `one_off`→`run_at`(datetime-local);`scheduled/ongoing` 有 `cron`→cron 文本;
  有 `interval_seconds`→间隔(秒)数字。
- **cron ↔ interval 模式切换(Tier 1)**:非 one_off 任务顶部有「间隔 / Cron」分段
  切换,`mode` 是可变 state(初值 `originalMode`)。切到另一模式提交时,即便值同名
  也强制回传新模式字段(`mode !== originalMode`),后端据此清掉另一个字段。one_off
  固定 run_at,不给切换(真正的 job_type 互转不在此范围)。
- **只回传 diff**:同模式下逐字段和原值比对,只把改动的字段交给 onSave,与后端
  `exclude_none`(未给的字段不覆盖 trigger_config)对齐;跨模式切换则必带新字段。
- **时区**:`<select>` 选项用 `Intl.supportedValuesOf('timeZone')` 动态生成
  (Tauri webview / 现代浏览器均支持),降级到常用集合;当前时区若不在列表则置顶。
- **过去时间校验**(one_off):`nowInTz(tz)` 用 `Intl.DateTimeFormat` 算出所选时区
  的当前 naive ISO,再和输入做字符串比较(ISO-8601 naive 串比较即时序比较),
  避免引入日期库。
- datetime-local 的值是 `YYYY-MM-DDTHH:mm`,回填时把存储值 slice(0,16),提交时
  补 `:00` 还原成后端要的 naive ISO。

## Gotchas

- 组件不持有"保存中/失败"的网络状态语义,`saving` 由父层 `JobsPanel` 传入;
  失败提示也在父层(用 ApiError.message)。这里只做表单 + 本地校验。
- 依赖 `trigger_config` 字段名与后端一致(`cron` 而非 `cron_expression`)——见
  [[api]] types 修复(2026-07-30)。
