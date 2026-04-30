---
code_file: src/xyz_agent_context/agent_framework/model_catalog.py
last_verified: 2026-04-29
stub: false
---
# model_catalog.py — 静态模型元数据与默认配置库

## 为什么存在

Settings 页面需要知道"NetMind 支持哪些模型"、"text-embedding-3-large 的维度是多少"、"GPT-5.1 的最大输出 token 是多少"。这些信息如果分散在各个 SDK 文件里会很难维护。这个文件把所有已知模型的元数据（维度、最大 token）和各 provider 的默认模型列表集中管理，供 `provider_registry.py`、`user_provider_service.py`、`openai_agents_sdk.py` 以及前端 API 查询。

## 上下游关系

被 `provider_registry.py` 调用来预填充新 provider 的模型列表（`get_default_models(source, protocol)`）。被 `openai_agents_sdk.py` 调用来获取 `max_output_tokens`，避免超出模型限制。被 `backend/routes/` 的 provider 相关 API 路由调用来返回 embedding model 列表和 suggested model 列表给前端。

无下游依赖——这是一个纯数据文件，不 import 任何其他系统模块。

## 设计决策

**纯静态数据，不查询 API**：不做动态 model discovery（如调用 OpenAI `/models` 接口），避免在初始化路径上引入网络依赖。代价是模型列表需要手动维护，新模型上线后要更新这个文件。

**按 (source, protocol) 二维键组织默认模型**：NetMind 的 Anthropic 和 OpenAI 协议支持的模型列表是完全不同的，不能只按 source 组织。这个设计让 `provider_registry` 在创建 provider 时能准确预填充正确协议的模型。

**`max_output_tokens` 设为模型上限的 90% 左右**：注释里说明了这个值是"90% of model limit"，留了安全边距，避免因提示词稍长而频繁触发截断错误。新加的 model 如果没有"独立验证"过的 token 上限，**留 None** —— 调用方会回退到 provider 自己的 cap，比胡乱填更安全。

**`is_official_provider()` 检查用于测试策略分流**：`provider_registry.py` 在做连接测试时，官方端点用 GET /models（零 token 消耗），非官方端点用 POST 真实 chat completion 请求（min token）。这个分流依赖 `OFFICIAL_BASE_URLS` 字典。

## Gotcha / 边界情况

- 如果用户配置了 catalog 里没有的 model，`get_max_output_tokens()` 返回 `None`，`openai_agents_sdk.py` 会不传 `max_tokens` 参数给 API（让 API 用默认值）。这是安全降级，不是错误。
- `get_official_models()` 和 `get_suggested_models()` 都查同一个 `_SUGGESTED_MODELS` 字典，返回结果相同，只是语义名称不同（给不同调用场景的 API 用）。

## 新人易踩的坑

- 新增 provider 预设时（如新的 proxy 服务），需要同时在 `_DEFAULT_MODELS`、`provider_registry.py` 的 builder 函数、`user_provider_service.py` 的 `_DUAL_PROVIDER_CONFIGS` 三处同步更新。这三处没有共享常量，容易遗漏一处。
- `ModelMeta.max_output_tokens` 单位是 token，不是字符，但名称容易让人混淆。`openai_agents_sdk.py` 把这个值传给 `max_completion_tokens` 参数。

## 新增一个 NetMind / 类似 provider 的 model — 三步 SOP

这是改动这个文件最常见的场景。按这个顺序操作不会漏：

1. **注册元数据**：在对应 provider 的 `_register(ModelMeta(...))` 块里加一行。`max_output_tokens` 没核实过就留空。
2. **加进默认列表**：在 `_DEFAULT_MODELS[(source, protocol)]` 对应的 list 里追加 model_id。注意一个 model 可能同时出现在 `("netmind", "openai")` 和 `("netmind", "anthropic")` 两个键下，按需要加哪个就加哪个。
3. **同步老用户**（如果该 provider 已经有用户在用）：跑

   ```bash
   uv run python scripts/backfill_netmind_default_models.py --dry-run   # 预览
   uv run python scripts/backfill_netmind_default_models.py             # 写入
   ```

   脚本是幂等的——已经包含了的 model 会被识别为 `[OK]` 跳过；只追加缺失项到 `models` JSON 数组末尾。该脚本目前硬编码 `source="netmind"` + 双协议遍历；如果以后要给 yunwu / openrouter 也做同样的事，复制这个脚本改 source 即可。

后端**必须重起**才能让新 model 的元数据进入 catalog 缓存——`_KNOWN_MODELS` 和 `_DEFAULT_MODELS` 是模块级的，import 时初始化一次。前端再刷一下页面，Settings 下拉就能看到新 model。
