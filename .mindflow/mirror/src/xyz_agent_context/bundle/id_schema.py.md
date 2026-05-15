---
code_file: src/xyz_agent_context/bundle/id_schema.py
last_verified: 2026-05-15
stub: false
---

# id_schema.py — ID Rewrite Layer 1 (PRD §8.11)

## 为什么存在

Bundle import 必须重生所有 ID（议题 1+5 决策）。要做到这一点必须**先有一份 ID 类型注册表** —— 每种 ID 的前缀 + 8-16 hex 长度的正则。这份表是 5 层防御的"唯一真理源"，新增 ID 类型只在这里加一行。

## 2026-05-15 新增 `artifact` kind

`art_[0-9a-f]{8,16}`。注意：`artifact_runner.py` 现存代码用 `secrets.token_hex(4)` = 8-hex，bundle 端 `gen_new_id("artifact")` 用 `secrets.token_hex(6)` = 12-hex；范围 8..16 同时覆盖两套。

## 上下游关系

- **被谁用**：
  - `bundle/importer.py` — 拼总 regex 扫自由文本
  - `bundle/id_field_map.py` — 通过 `ID_KIND_PREFIXES` 生成新 ID
- **依赖谁**：仅 stdlib `re`

## 设计决策

`ID_KINDS` dict 是简单 `{kind_name: regex_pattern}` 映射，不用类不用工厂。加新 ID 类型只需 `"foo": r"foo_[0-9a-f]{8,16}"` 一行。

`build_all_id_regex()` 拼 alternation，`importer.py` 用它做 Layer 4 自由文本扫描。
