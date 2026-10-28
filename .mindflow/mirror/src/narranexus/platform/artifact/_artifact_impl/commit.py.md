---
code_file: src/narranexus/platform/artifact/_artifact_impl/commit.py
last_verified: 2026-10-28
stub: false
---

# commit.py — 内容变更提交尾巴的唯一 owner

## 为什么存在

pr-self-review 自查(2026-08-20)揪出的手抄漂移:user_edit 的两条路径与
freshness 检测各自复制了同一段「update_pointer→history→事件」尾巴,
三份已在 size 语义上出现措辞分叉。收敛为 `commit_content_refresh`——
调用方只提供 new_hash 与 history_action(user_edited/external_edited),
size 规则(顶层单文件=文件本身,专属目录=整目录)与事件形状只活在
这里。**指针永不因内容提交移动**(file_path 原样写回)。

## 2026-10-28（PR #421 复审 I1）— 必填 `abs_entry`，本模块不再自己 join

`commit_content_refresh` 原来自己 `os.path.join(base_working_path, file_path)` 再量 size——
裸 join 不做 realpath 比对，路径上任何链接都会被跟随：freshness 检测经它对**受害者目录**
`_dir_size` 求和并写回攻击者自己的行。现在调用方传入已经过 [[confinement]] `confine_entry`
确权的 realpath（`abs_entry`，keyword 必填），本模块只量、只记。两次独立解析本身就是
"同一事实两处表达"，不确权的那份正是出事的那份。调用方：[[user_edit.py]] 两处、
[[freshness.py]]（heal 的 `_repoint` 走 [[registration.py]]，不经这里）。
**不要**在这里放宽 `except` 去吞 `ArtifactPathEscape`——越界由调用方在进入前处理。

## 上下游

调用方:[[user_edit.py]](save_user_content / commit_office_user_edit)、
[[freshness.py]](refresh_external_state,external=True)。注册/重注册
不走这里——那是 [[registration.py]] 的领土(它还管 kind 校验与路径
确权,尾巴形状不同)。
