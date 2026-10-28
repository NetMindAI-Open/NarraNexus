#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
General text processing utilities

@file_name: text.py
@author: NetMind.AI
@date: 2025-12-22
@description: Provides general text processing functions such as keyword extraction and text truncation

Features:
1. extract_keywords - Extract keywords from text (supports Chinese and English)
2. truncate_text - Smart text truncation
3. strip_routing_prefix - Drop the "[From <sender>]" channel label before BM25
"""

from __future__ import annotations

import re
from typing import List, Set, Optional


# =============================================================================
# Stop Words
# =============================================================================

# Chinese stop words
CHINESE_STOPWORDS: Set[str] = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
    "这", "那", "有", "和", "就", "不", "人", "都", "一", "上",
    "也", "很", "到", "说", "要", "去", "吗", "会", "着", "没", "看",
    "好", "自己", "这个", "那个", "怎么", "什么", "如何", "为什么",
    "可以", "能", "想", "知道", "觉得", "应该", "可能", "需要",
    "请", "帮", "帮我", "告诉", "一下", "一些", "还是", "或者",
}

# English stop words
ENGLISH_STOPWORDS: Set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "and", "or", "but", "not",
    "with", "from", "by", "as", "this", "that", "it", "its", "i", "you",
    "he", "she", "we", "they", "my", "your", "his", "her", "our", "their",
    "what", "how", "why", "when", "where", "which", "who", "whom",
    "can", "could", "would", "should", "will", "do", "does", "did",
    "have", "has", "had", "am", "if", "then", "so", "than", "just",
    "about", "into", "over", "after", "before", "between", "under",
    "again", "further", "once", "here", "there", "all", "each", "few",
    "more", "most", "other", "some", "such", "no", "nor", "only", "own",
    "same", "too", "very", "just", "also", "now", "please", "help", "me",
}

# Combined stop words
ALL_STOPWORDS: Set[str] = CHINESE_STOPWORDS | ENGLISH_STOPWORDS


# =============================================================================
# Keyword Extraction
# =============================================================================

# Channel routing prefix, as built by
# ``channel.channel_context_builder_base.build_channel_anchor`` and by
# ``message_bus.message_bus_trigger`` ("[From agent <id>] <body>"). The two
# must stay a matched pair — the round-trip is pinned in
# tests/narrative/test_routing_prefix_strip.py.
#
# Deliberately anchored at the very start and bounded to one bracket: the
# sender label is only metadata when it PREFIXES the turn. `message_bus`
# joins several messages, each line carrying its own label, and those
# interior labels are ~1% of the terms in a 250-term bus query whose
# evidence is broad and healthy. Widening this would lower correct scores
# for no measured gain.
_ROUTING_PREFIX_RE = re.compile(r"^\[From (?:[^\]\n]{0,160})\]\s*")


def strip_routing_prefix(text: Optional[str]) -> str:
    """Drop the leading "[From <sender>] " label from a retrieval query.

    WHY this is not cosmetic: `tokenize` turns ``[From Liam] 👊`` into
    ``['from', 'liam']`` — the emoji contributes nothing — so BM25 scored a
    fist-bump at 5.66 and cleared RAW_FLOOR=3.0 on sender name alone (prod
    audit 768). Measured over the 2026-08-14..20 audit table, 96% of queries
    carry such a prefix and **30.5% of prefix-carrying decisions fall to a
    top1 of zero once it is removed**: the metadata was the whole match.

    The sender still reaches the tiers that should see it — the judge and the
    continuity detector read the untouched query text. Only the BM25 surface
    is cleaned, because BM25 is the one tier that cannot tell a name from a
    topic.

    Returns "" for None/empty. A prefix-only message legitimately strips to
    nothing: the honest BM25 score for "a sender said an emoji" is no score,
    which routes the turn to the judge.
    """
    return _ROUTING_PREFIX_RE.sub("", text or "", count=1)


def extract_keywords(
    text: str,
    max_keywords: int = 5,
    min_length: int = 2,
    stopwords: Optional[Set[str]] = None
) -> List[str]:
    """
    Extract keywords from text

    Supports mixed Chinese and English text, automatically filters stop words and short words.

    Args:
        text: Input text
        max_keywords: Maximum number of keywords (default 5)
        min_length: Minimum word length (default 2)
        stopwords: Custom stop word set (defaults to built-in stop words)

    Returns:
        List of keywords (deduplicated, order preserved)

    Example:
        >>> extract_keywords("How to use Python for machine learning?")
        ['Python', 'machine', 'learning']
        >>> extract_keywords("How to build a recommendation system?")
        ['build', 'recommendation', 'system']
    """
    if not text:
        return []

    # Use default stop words
    if stopwords is None:
        stopwords = ALL_STOPWORDS

    # Extract words (mixed Chinese and English)
    # Match: Chinese character sequences or English alphanumeric sequences
    words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z0-9]+', text)

    # Filter stop words and short words
    keywords = []
    seen = set()

    for word in words:
        word_lower = word.lower()

        # Skip stop words
        if word_lower in stopwords or word in stopwords:
            continue

        # Skip short words
        if len(word) < min_length:
            continue

        # Deduplicate (preserve original case)
        if word_lower not in seen:
            keywords.append(word)
            seen.add(word_lower)

            # Reached maximum count
            if len(keywords) >= max_keywords:
                break

    return keywords


# =============================================================================
# Text Truncation
# =============================================================================

def truncate_text(
    text: str,
    max_length: int = 100,
    suffix: str = "..."
) -> str:
    """
    Smart text truncation

    If the text exceeds the maximum length, truncate at an appropriate position and add a suffix.

    Args:
        text: Input text
        max_length: Maximum length (default 100)
        suffix: Truncation suffix (default "...")

    Returns:
        Truncated text

    Example:
        >>> truncate_text("This is a very long text", max_length=10)
        'This is...'
    """
    if not text:
        return ""

    if len(text) <= max_length:
        return text

    # Calculate actual available length
    available_length = max_length - len(suffix)
    if available_length <= 0:
        return suffix

    return text[:available_length] + suffix


