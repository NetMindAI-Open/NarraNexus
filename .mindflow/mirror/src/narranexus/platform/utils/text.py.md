---
code_file: src/narranexus/platform/utils/text.py
last_verified: 2026-08-20
stub: false
---
# text.py

Lightweight text processing utilities — keyword extraction and smart truncation for mixed Chinese-English content.

## Why it exists

Several parts of the system (Narrative topic tracking, Module embedding, agent context building) need to extract keywords from user messages or module output. Rather than using a heavy NLP library (jieba, spaCy) that would add significant dependencies and startup time, `text.py` provides a regex-based keyword extractor that is fast enough for real-time use and handles both Chinese and English text. `truncate_text` addresses the need to safely shorten context strings before embedding or prompt injection.

## `strip_routing_prefix` — 为什么一个文本工具里会有路由知识

2026-08-20 加入。它剥掉查询开头的 `[From <sender>] ` 标签。

放在这里而不是 `narrative/` 里,是因为它是 `channel.channel_context_builder_base.
build_channel_anchor` 的**逆函数**,而 narrative 不能反向依赖 channel(铁律 #3
的方向纪律)。两者是一对,格式由 `build_channel_anchor` 定义 ——
`tests/narrative/test_routing_prefix_strip.py::test_is_the_inverse_of_build_channel_anchor`
钉住这个往返,谁改了格式那条断言先响。

**为什么必须剥**:`tokenize('[From Liam] 👊')` → `['from', 'liam']`,表情分词后
什么都不剩。prod 审计 768 行:这条查询打了 5.66 分,**100% 来自 `from`+`liam`**,
过了 `RAW_FLOOR=3.0`。2026-08-14..20 的 26,922 行审计里 96% 的查询带这个前缀,
**其中 30.5% 剥掉前缀后 top1 归零** —— 元数据就是全部证据。

**只剥开头那一个**。`message_bus` 会把多条消息逐行拼起来,每行一个标签;
那些行内标签在一条 250 词的 bus 查询里只占 ~1%,而 bus 流量的证据形态是健康的
宽面重合(最大词占比中位 0.03)。扩大剥离范围会压低正确分数,没有实测收益。
依据:`specs/2026-08-20-bm25-gate-redesign-research.md` §R2.1。

**谁看得到未剥的原文**:judge 与 continuity 两层。它们分得清"人名"和"话题",
BM25 分不清 —— 所以只清理 BM25 那一路。

## Upstream / Downstream

**Consumed by:** `narrative/` (topic keyword extraction from events), `module/` (generating embedding hints), `context_runtime/` (truncating long strings before prompt assembly), and any other code that imports from `utils/__init__.py` (which re-exports both functions).

**Depends on:** stdlib `re` only. No ML libraries.

## Design decisions

**Regex-based, not NLP-based.** The extractor uses `re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', text)` to split on character type boundaries. For the use case of extracting topic keywords from short conversational text, this is sufficient and avoids adding jieba or other tokenizers as dependencies.

**Hardcoded stop-word sets for Chinese and English.** `CHINESE_STOPWORDS` and `ENGLISH_STOPWORDS` are module-level sets of the most common function words. The sets are deliberately minimal — they filter out noise without needing an external resource file.

**Deduplication preserves original case.** The `seen` set tracks lowercased forms for deduplication, but the returned keyword list preserves the original capitalization from the text. This matters for proper nouns.

**`truncate_text` does not split on word boundaries.** It truncates at exactly `max_length - len(suffix)` characters. This can split a word mid-character in Chinese text. The implementation is intentionally simple because precision truncation is not required for any current caller.

## Gotchas

**Stop words are English-centric.** The English stop-word set is tuned for conversational English. Technical terms (e.g., "model", "type", "key") are not in the stop list and will appear as keywords. For agent context that is heavily technical, these may dilute the useful keywords.

**Chinese word boundaries are not respected.** The regex matches continuous Chinese character sequences as single tokens. A multi-character Chinese word like "人工智能" (artificial intelligence) is returned as one token, which is correct. But a two-character sequence that spans a meaningful boundary (e.g., "的我") would also be returned as one token if long enough. For the current use case (short conversational snippets), this is acceptable.

**New-contributor trap.** The `min_length` default is 2, meaning single-character tokens are filtered out. Single-character Chinese words (like "我", "你") are in the stop list anyway, but single-character English words ("a", "I") that are not in the stop list would also be filtered. This is generally desirable but can surprise callers who pass `min_length=1`.
