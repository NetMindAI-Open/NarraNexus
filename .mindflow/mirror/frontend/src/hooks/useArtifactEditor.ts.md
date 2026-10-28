---
code_file: frontend/src/hooks/useArtifactEditor.ts
last_verified: 2026-08-20
stub: false
---

# useArtifactEditor.ts — 常驻编辑面状态机

## 为什么存在

Spec A §3/§4 的编辑状态机,与 UI 解耦(ResidentTextEditor 是壳,
md 块编辑器 C3 复用):load(bytes→text+hash)→ dirty → save(锁)→
409 二选(overwrite/discard)。

## 两条防丢规则(都是「击键永不静默消失」)

1. **dirty 编辑器忽略外部刷新**:agent 改文件→updated_at→raw url
   重铸→若 reload 会砸掉用户正打的字。dirty 时跳过 reload(AionUI
   dirty-skip 同构),分歧推迟到保存时的 409,由用户决定。
2. **localStorage 草稿**:setText 直写 `narra:artifact-draft:{id}`,
   重挂载且盘上 base 未变时恢复(dirty+draftRestored 横幅);base 变了
   =机器上无法合并,丢弃草稿(如实的取舍,别改成静默保留)。

## 坑

- 锁 base=加载字节的 hash,**不是** artifact.content_hash(表可能落后
  于盘);保存成功后用响应的 content_hash re-base。
- textRef/dirtyRef:save 回调不随击键换身份;load effect 不把 dirty
  当依赖(url 变才触发)。

## 2026-08-20 — 加载解码 fatal(#334 I4)

TextDecoder fatal:true——解不动的文档不进编辑面(错误态=只读),
有损解码在首存被写回的路径整个消灭。HtmlRenderer 的提交侧同改
(解码失败=anchor-failed→交 AI 横幅,绝不 PUT)。

## 2026-08-20 — 草稿层显式失效 + 陈旧清理(#334 I8)

512KB 体积门:装不下→writeDraft 返回 false→`draftUnavailable` 状态→
红横幅「文件过大,草稿不保,及时保存」——**静默降级是唯一不可接受的
选项**;beforeunload 照拦。草稿带 ts,每会话一次清理 14 天陈旧项
(刻意**不**按 artifact 列表删——store 只知当前 agent,按列表会误杀
其他 agent 的活草稿);删除 artifact(本地或事件)在 store.remove 里
顺手删对应草稿键。

## 2026-08-20(二)— 草稿实现驻地移交 [[drafts]](#334 r2 I5/r3 M6)

键模板/512K 门(code unit 语义)/删除/陈旧清理的单一 owner 在
lib/artifactEditing/drafts.ts(无 React);hook 只消费。**清理先于
恢复由控制流保证**:loadFrom 在 readDraft 前调 sweepStaleDrafts
(幂等),不再依赖 effect 声明顺序(r3 M2)。
