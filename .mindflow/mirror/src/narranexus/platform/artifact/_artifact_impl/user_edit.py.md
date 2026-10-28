---
code_file: src/narranexus/platform/artifact/_artifact_impl/user_edit.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 写入前先过属主限定

`save_user_content` 与 `commit_office_user_edit` 原来 `os.path.join(base, file_path)` 直接
写，完全没有范围限制：导入包塞进来的 `../` 指针能覆盖别人的文件，409 返回的
current_hash 还能当探针。现在两处都先经 [[confinement]] 的 `confine_entry`，越界一律
`ArtifactNotFound`（与路由 404 口径一致），之后才检查存在性和 hash。
确权得到的 realpath 原样作为 `abs_entry` 交给 [[commit.py]]（PR #421 复审 I1）——
提交尾巴不再自己二次 join。

# user_edit.py — 用户编辑保存管线

## 为什么存在

Spec A §3:编辑面(常驻编辑器/md 块编辑器/html 逐元素)的**唯一提交
路径**。形状:base_hash 乐观锁 → 临时文件+os.replace 原子写 → 指针行
刷新(hash/size/updated_at,**file_path 不变**——用户编辑永不移指针)→
history action="user_edited" → stage "updated" 事件。

## 设计决断

- **锁对盘不对表**:比较对象是磁盘现内容的 hash,不是表指纹——盘是
  真身,外部写者可能已越过表;拿表指纹会让保存静默覆盖外部编辑
  (test_conflict_verifies_against_disk_not_table 钉死)。
- 前端做锚定替换后**整文件 PUT**,服务端无锚定逻辑——锚定失败在
  客户端立刻可见,409 语义全 kind 统一。
- EDITABLE_KINDS=(md/csv/html):与 kindRegistry editSurface 对应;
  office 走 officecli 命令翻译(Spec B),二进制无文本可 PUT。
- 临时文件必须与 entry **同目录**(os.replace 不跨文件系统),失败路径
  unlink 清理——半截文件或 .tmp 掉在服务目录里都会被 raw 路由端出去。

## 上下游

service 桥:[[artifact_service.py]] save_user_content;路由:
backend/routes/agents/artifacts.py PUT /content;复用
[[registration.py]] 的 MAX_ARTIFACT_BYTES/_dir_size/_record_history/
compute_entry_hash 与 [[notify.py]] 的 stage_artifact_event。

## 2026-08-19(二)— commit_office_user_edit

office watch 页用户编辑的提交点:字节已由 watch 常驻写入(单写者
串行化),本函数只刷新登记(hash/size/updated_at+history user_edited
+事件)。**hash 未变=幂等跳过**——前端回调重复触发不会灌 history。
kind 门:仅 application/vnd.officecli-live。

## 2026-08-20 — 提交尾巴移交 [[commit.py]]

两条路径的「update_pointer→history→事件」尾巴与 freshness 的第三份
拷贝收敛到 commit_content_refresh;本文件只剩各自的门(锁/kind/幂等)
与写盘。
