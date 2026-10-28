"""
@file_name: __init__.py
@author: NarraNexus
@date: 2026-08-28
@description: Backend-side install orchestration for the optional
              coding-agent framework plugins (Claude Code, Codex CLI).

On the local build (bash run.sh / desktop DMG) these two frameworks are no
longer bundled — the user installs them on demand from Settings -> Plugins.
This package owns the platform-side half of that flow: what to install
(spec.py, registry.py), how to install it (``_installers/``), how to explain
a failure (errors.py), and the public facade routes call (service.py).

Consumed exclusively by backend routes/lifespan (binding rule #21) — never
imported from ``narranexus.platform``. The actual install LOCATIONS (where the
pyenv/nodejs trees live) are owned by
``narranexus.platform.agent_framework.plugin_paths``, which this package
imports rather than re-deriving.
"""
