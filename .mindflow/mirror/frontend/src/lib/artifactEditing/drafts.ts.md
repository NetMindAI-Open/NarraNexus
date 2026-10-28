---
code_file: frontend/src/lib/artifactEditing/drafts.ts
last_verified: 2026-08-20
stub: false
---

# drafts.ts — 编辑草稿层的唯一驻地(#334 r2 I5)

键模板/体积门/删除/陈旧清理收拢一处:store 曾手抄键模板字面量——
同一事实两处拼写,改前缀只会改到一处。无 React(zustand store 消费
removeDraft 不拖 hooks 进模块图)。

## 契约

- `DRAFT_MAX_CHARS = 512K`:量的是 **UTF-16 code unit**(String.length),
  CJK 文档 ≈1.5MB 存储,仍低于 ~5MB 配额(r2 M3:旧名 BYTES 会误导
  调参者)。writeDraft 返回 false=没存上,调用方必须亮
  draftUnavailable,不许吞。
- `sweepStaleDrafts`:每会话一次;**只用正则读 ts**(整份 JSON.parse
  512K 草稿会在编辑面挂载瞬间卡主线程,r2 M4);无 ts 视为陈旧;
  **清理先于恢复是声明的契约**(loadFrom 在 readDraft 之前调用,同一函数内控制流保证——与 [[useArtifactEditor.ts]] 措辞互证),不是 effect
  顺序的巧合。刻意不按 artifact 列表删(store 只知当前 agent)。

## 2026-08-20(二)— writeDraft 失败即作废(#334 r3 I3/r4 C3)

每条返回 false 的路径(超限/配额异常)都**先 removeItem 再返回**:残留
旧草稿的 baseHash 仍匹配,下次挂载会以「已恢复未保存修改」横幅交还
一份**更旧**的文本——比诚实地什么都没有更坏。那两行 removeItem 读
起来像多余代码,删掉它没有测试会立刻提示动机——它靠本条 md 与
drafts.test.ts 的两个失败路径用例守着(配额用例自装 Storage 假件:
CI 的真 jsdom Storage 上 spy 挂不上,r4 C1 教训)。
