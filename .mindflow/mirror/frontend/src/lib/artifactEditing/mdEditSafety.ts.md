---
code_file: frontend/src/lib/artifactEditing/mdEditSafety.ts
last_verified: 2026-08-20
stub: false
---

# mdEditSafety.ts — md 块编辑器的防丢守卫

## 为什么存在

2026-08-19 spike 实证:字节级往返在块编辑器世界不存在(remark 系
serializer 必然规范化列表符号/表格分隔线/强调标记),而 frontmatter
会被**摧毁**(解析成分割线+标题,真数据丢失)。守卫因此是语义的:

- `extractFrontmatter`:不解析 YAML,纯字面切分(首行 `---`、无空行、
  `---`/`...` 收口),保证 frontmatter+body 逐字节重组回原文。
- `mdAstEqual`:remark-parse+gfm 双方解析,剥 position 后比树——
  结构/文字变化=不等价(停用编辑);纯风格差=等价(放行)。

## 实测边界(2026-08-19 spike,Crepe)

无损:html 块、html 注释、GFM 脚注、数学 $..$/$$..$$、:::directive、
setext 标题(风格级)。**有损:reference 式链接**(linkReference+
definition 被解成内联 link)——守卫测试用它当样本。守卫是探针不是
黑名单:Crepe 升级后边界自动跟随。

## 坑

parser 崩溃 → 返回不等价(不能担保就不放行),别改成放行。

## 2026-08-20 — extractFrontmatter 行尾**免疫**(#334 I7→r2 I2→r3 I2 终态)

三轮演化史,防有人写回旧版:①「按文档 eol 推断」= r2 判死刑的形态
(正文一行 CRLF 就翻转围栏比较,LF frontmatter 静默进 Crepe);
②「按开场行推断」= r3 判死刑(围栏块**内部**混行尾仍崩)。终态=
**不推断 eol**:恒按 `\n` 切(各行保留自己的 `\r`),围栏行剥**一个**
尾随 `\r` 后比较,`join('\n')` 重组——任意行尾组合下字节恒等,
这一类不再有第四个反例。`\r\r\n` 病态围栏刻意判非 frontmatter
(strip 只削一个)。保存时的 LF 归一政策仍归 MarkdownRenderer。

## 2026-08-20(二)— 字节恒等的一个声明例外(#334 r4 M2)

闭合围栏为末行且无结尾换行时,frontmatter 补一个换行(重组比原文多
一字节)——对 YAML 消费者无害,历代实现行为一致;docstring 同步声明,
不再写「任意组合恒等」的满话。
