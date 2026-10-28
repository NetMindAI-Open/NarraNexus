"""Agent profile — the platform's own writer for an agent's name and description.

A domain package rather than a Module: the transaction writes the ``agents``
row and ``bus_agent_registry`` and is depended on by core HTTP routes, so
living inside a hot-pluggable Module made that Module un-unpluggable (铁律 #3):
unregistering it was an ImportError at route import, so the backend would not
start rather than one feature degrading.

The Awareness step it still needs is reached through a deferred import — which
buys ownership, not isolation. Python imports parent packages, so that call
still loads the whole module_registry; what it removes is any module-scope dependency
from this package or the routes onto the Module layer.
"""
from .agent_profile_service import AgentProfileWrite, apply_agent_profile_change

__all__ = ["AgentProfileWrite", "apply_agent_profile_change"]
