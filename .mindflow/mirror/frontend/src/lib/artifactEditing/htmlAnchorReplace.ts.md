---
code_file: frontend/src/lib/artifactEditing/htmlAnchorReplace.ts
last_verified: 2026-08-20
stub: false
---

# htmlAnchorReplace.ts — 锚定字面替换(html 逐元素)

inner 唯一→替换;inner 零命中→not-found(脚本生成,降 AI);inner 多
义→扩到 outerHTML,唯一→在其内替换(搜索从开标签 '>' 之后起,防
属性同文误中),仍多义→ambiguous。**绝不最近似替换**——错位编辑比
拒绝更糟(测试钉死)。no-change 拒绝(无事可存)。

## 2026-08-20 — 锚定优先级反转(#334 I5)

**outer 优先**:outerHTML 是更强的锚,先用它(内部替换仍从开标签 '>'
之后搜,防同元素属性复述);outer 多义→拒。outer 找不到是**常态**
(浏览器重排属性顺序/引号),inner 回退保留——但唯一命中还必须落在
**文本位置**(前一个非空白字符是 '>'),属性值里的唯一命中(alt/title/
aria-label 复述可见文案)一律拒。alt 反例测试钉死。
