"""
@file_name: distribution.py
@author: Bin Liang
@date: 2026-09-04
@description: The ``narranexus-dist.json`` contract (spec section 19.3): a distribution is a declaration — engine range, plugin set, excludes, branding, auth, defaults, runtime shape, build targets and slot bindings. Parsing only; resolution against the host lives in the kernel.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from narranexus.contracts._base import PluginError

DISTRIBUTION_FILENAME = "narranexus-dist.json"
# The one plugin-id grammar (the kernel's manifest validation and the index
# validator import it): [a-z0-9] words joined by single '_'/'-', at least two
# dot-separated segments; no leading/trailing/doubled separators, so the
# flattened id terminated by '__' is an injective table / env prefix.
PLUGIN_ID_RE = re.compile(r"^[a-z0-9]+([_-][a-z0-9]+)*(\.[a-z0-9]+([_-][a-z0-9]+)*)+$")
DISTRIBUTION_ID_RE = PLUGIN_ID_RE
BUILTIN_PREFIX = "builtin."


def is_builtin_id(plugin_id: str) -> bool:
    """Whether ``plugin_id`` is a builtin (engine-maintained) plugin.

    The ONE predicate for the load-bearing "is this a builtin?" question —
    worker naming, UI badges, disable permissions, boot isolation, route
    trust all ask it here instead of each spelling the prefix.
    """
    return plugin_id.startswith(BUILTIN_PREFIX)

Deployment = Literal["desktop", "cloud", "headless"]
Target = Literal["desktop", "docker", "wheel"]


class DistributionError(PluginError):
    """A distribution declaration that cannot be used (bad shape or unresolvable)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PluginRef(_Strict):
    """How one plugin enters the distribution: a version range (from the registry) or a local path (bundled)."""

    range: str = "*"
    path: str = ""

    @property
    def bundled(self) -> bool:
        return bool(self.path)


class Branding(_Strict):
    name: str = ""
    logo: str = ""
    theme: str = ""


class Defaults(_Strict):
    pipeline_profile: str = Field(default="default", alias="pipelineProfile")
    agent_spec: str = Field(default="", alias="agentSpec")
    locale: str = "en"


class RuntimeSpec(_Strict):
    user_plugins: bool = Field(default=True, alias="userPlugins")
    deployment: Deployment = "desktop"


class DistributionSpec(_Strict):
    """One ``narranexus-dist.json``."""

    id: str
    display_name: str = Field(alias="displayName")
    description: str = ""
    engine: str = "*"
    plugins: Mapping[str, PluginRef] = Field(default_factory=dict)
    excludes: tuple[str, ...] = ()
    branding: Branding = Field(default_factory=Branding)
    auth: str = "builtin.auth.local"
    defaults: Defaults = Field(default_factory=Defaults)
    runtime: RuntimeSpec = Field(default_factory=RuntimeSpec)
    targets: tuple[Target, ...] = ("desktop",)
    bindings: Mapping[str, str | tuple[str, ...]] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not DISTRIBUTION_ID_RE.match(v):
            raise ValueError(f"id must be '<publisher>.<name>' in [a-z0-9_.-], got {v!r}")
        return v

    @field_validator("plugins", mode="before")
    @classmethod
    def _plugin_refs(cls, v: Any) -> Any:
        if not isinstance(v, Mapping):
            raise ValueError("plugins must be an object of plugin id -> version range | {path}")
        out: dict[str, Any] = {}
        for pid, ref in v.items():
            if not PLUGIN_ID_RE.match(str(pid)):
                raise ValueError(f"plugins: {pid!r} is not a plugin id")
            if isinstance(ref, PluginRef):
                out[pid] = ref
            elif isinstance(ref, str):
                out[pid] = {"range": ref or "*"}
            elif isinstance(ref, Mapping):
                out[pid] = dict(ref)
            else:
                raise ValueError(f"plugins[{pid!r}]: give a version range string or {{\"path\": ...}}")
        return out

    @field_validator("excludes", "bindings", mode="before")
    @classmethod
    def _ids(cls, v: Any) -> Any:
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "DistributionSpec":
        both = sorted(set(self.excludes) & set(self.plugins))
        if both:
            raise ValueError(f"excludes and plugins overlap: {both}")
        for pid in self.excludes:
            if not PLUGIN_ID_RE.match(pid):
                raise ValueError(f"excludes: {pid!r} is not a plugin id")
        if not PLUGIN_ID_RE.match(self.auth):
            raise ValueError(f"auth: {self.auth!r} is not a plugin id")
        if self.runtime.deployment == "cloud" and self.runtime.user_plugins:
            raise ValueError("runtime.userPlugins must be false for a cloud deployment (spec D1: no runtime plugins on cloud)")
        if "kernel.auth" in self.bindings and self.bindings["kernel.auth"] != self.auth:
            raise ValueError("bindings['kernel.auth'] disagrees with 'auth'; set one of them")
        return self

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(self.plugins)


def parse_distribution(data: Mapping[str, Any], *, origin: str = DISTRIBUTION_FILENAME) -> DistributionSpec:
    try:
        return DistributionSpec.model_validate(dict(data))
    except ValidationError as exc:
        lines = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors())
        raise DistributionError(f"{origin}: {lines}") from None


__all__ = [
    "BUILTIN_PREFIX",
    "is_builtin_id",
    "PLUGIN_ID_RE",
    "DISTRIBUTION_FILENAME",
    "Branding",
    "Defaults",
    "Deployment",
    "DistributionError",
    "DistributionSpec",
    "PluginRef",
    "RuntimeSpec",
    "Target",
    "parse_distribution",
]
