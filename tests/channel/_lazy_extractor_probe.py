"""
@file_name: _lazy_extractor_probe.py
@author: Bin Liang
@date: 2026-09-07
@description: A stand-in reply extractor whose IMPORT is the assertion — test_message_source_handler checks this module stays out of sys.modules until the extractor is actually called.

It has to be a real, separately importable module: the laziness under test is
``importlib.import_module`` being deferred to first use, which cannot be
observed on a function defined inline in the test file.
"""
from __future__ import annotations

from typing import Any, Optional


def extract(tool_name: str, arguments: dict[str, Any]) -> Optional[str]:
    return arguments.get("text") or None
