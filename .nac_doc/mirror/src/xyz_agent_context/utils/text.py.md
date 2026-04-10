# text.py

Lightweight text processing utilities — keyword extraction and smart truncation for mixed Chinese-English content.

## Why it exists

Several parts of the system (Narrative topic tracking, Module embedding, agent context building) need to extract keywords from user messages or module output. Rather than using a heavy NLP library (jieba, spaCy) that would add significant dependencies and startup time, `text.py` provides a regex-based keyword extractor that is fast enough for real-time use and handles both Chinese and English text. `truncate_text` addresses the need to safely shorten context strings before embedding or prompt injection.

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
