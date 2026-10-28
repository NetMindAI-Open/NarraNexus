"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: Prompts group — the single home of every prompt the
framework speaks (the one exception: tool descriptions travel with
their ToolSpec — single source of truth against drift).

Shape (Owner decision): everything converges on the ``NexusPowerPrompts``
namespace class (library.py) — non-instantiable, classmethods return
strings, a subclass is a complete prompt pack. ``assembler.py`` owns
ordering and the stable-prefix / dynamic-tail split (cache constraint
C2); long-form copy lives in ``resources/*.md`` so wording changes
never touch code.
"""
