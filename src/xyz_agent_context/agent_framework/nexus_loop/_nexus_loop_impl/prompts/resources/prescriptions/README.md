<!-- S2 模型行为处方目录(设计稿) -->

按模型族一个 md 文件(Codex per-model prompt 目录形状), 例如:
- `anthropic.md` — thinking 块使用惯例
- `openai.md` — 强制工具使用特化段(Hermes 对 OpenAI 系的处方先例)
- `weak_models.md` — 弱模型的显式工具调用引导

装配时由 model_prescription_section 按 ModelParams.model 匹配加载;
无匹配返回空串。铁律 #15: 处方是通用行为引导, 不是对用户模型的限制。
