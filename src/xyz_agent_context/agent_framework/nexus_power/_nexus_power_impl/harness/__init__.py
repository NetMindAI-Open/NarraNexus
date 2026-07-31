"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: Harness group — the structural carrier of the agent's
thinking mode.

The framework's defining divergence lives here: assistant text is
inner monologue (self-thinking); reaching the user or the world
requires an explicit tool call. Consequences:
  1. stop semantics = "no more actions", not "stopped talking" (stop.py);
  2. text events ride the ui track as monologue, never as replies
     (expression.py stamps them);
  3. expressive tools are platform-granted and list-injected — the
     framework owns no voice; a channel-less agent is mute, not broken.
"""
