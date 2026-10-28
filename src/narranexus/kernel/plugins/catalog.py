"""
@file_name: catalog.py
@author: Bin Liang
@date: 2026-09-07
@description: The slot catalog: every extension slot grouped by domain, with its contract, arity, default, the candidates registered in this process (by owner and contribution name) and what is bound right now (provider + the layer/origin that bound it). One view for the CLI (``narranexus slots``), the docs, the factory API and the agent's self-awareness tools.
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.kernel.plugins.bound import bound_layer, bound_provider



def domains(tree: Any) -> list[tuple[str, str]]:
    """``(domain, title)`` in display order: the tree's root slots in declaration
    order (the kernel seeds them in display order — prompt right after the
    kernel, the first thing a distribution author wants to see they can
    replace — and a plugin's own namespace root follows), titled by the root
    slot's ``doc``."""
    return [(root.path, root.doc or root.path) for root in tree.roots()]


def _resolved(registries: Any, slot: str, arity: str) -> dict[str, Any]:
    resolved = getattr(registries, "bindings", None)
    if arity == "one":
        out: dict[str, Any] = {"provider": bound_provider(registries, slot), "layer": bound_layer(registries, slot)}
        if resolved is not None and slot in resolved.one:
            out["origin"] = resolved.one[slot].origin
        return out
    if resolved is not None and slot in resolved.many and resolved.many[slot].providers:
        b = resolved.many[slot]
        return {"providers": list(b.providers), "layers": [layer.name for layer in b.layers]}
    return {"providers": None, "layers": []}


def slot_catalog(registries: Any, *, domain: Optional[str] = None) -> list[dict[str, Any]]:
    """Domains in display order, each with its slots (path order) and their state."""
    tree = registries.slots
    groups: dict[str, list[dict[str, Any]]] = {}
    for path in tree.paths():
        head = path.split(".", 1)[0]
        if domain and head != domain:
            continue
        spec = tree.get(path)
        try:
            entries = list(registries.registry_for(path).entries())
        except Exception:  # noqa: BLE001 — a slot with no registry yet has no candidates
            entries = []
        groups.setdefault(head, []).append({
            "path": path,
            "arity": spec.arity,
            "contract": spec.contract,
            "default": spec.default,
            "distribution_only": spec.distribution_only,
            "stability": spec.stability.value,
            "doc": spec.doc,
            "candidates": sorted({e.owner for e in entries}),
            "contributions": [{"name": e.name, "owner": e.owner} for e in entries],
            "bound": _resolved(registries, path, spec.arity),
        })
    return [{"domain": d, "title": title, "slots": groups[d]} for d, title in domains(tree) if d in groups]


def toml_template(catalog: list[dict[str, Any]]) -> str:
    """A commented ``narranexus.toml`` listing every bindable slot with its candidates (distribution-only slots are shown but commented as not user-bindable)."""
    lines = [
        "# narranexus.toml — which plugin fills which slot (layer USER_CONFIG: above the distribution, below NX_BIND__* env).",
        "# One-arity slots take a plugin id; many-arity slots take an ordered list (order = effective order; unlisted = dropped).",
        "# Distribution-only slots (kernel.*, ui) can only be set by the distribution (narranexus-dist.json) — listed for reference.",
        "# Apply with a restart; `narranexus slots` shows what is bound and by which layer.",
        "",
        "[bindings]",
    ]
    for group in catalog:
        lines += ["", f"# ── {group['title']}"]
        for s in group["slots"]:
            cands = ", ".join(s["candidates"]) or "(nothing registered)"
            if s["arity"] == "one":
                value = f'"{s["bound"].get("provider") or ""}"'
            else:
                names = [f'"{c["owner"]}:{c["name"]}"' for c in s["contributions"]]
                value = "[" + ", ".join(names) + "]"
            prefix = "# "  # every line commented: the template documents, the user uncomments what to change
            lines.append(f"# {s['path']}: {s['doc']}  candidates: {cands}")
            lines.append(f'{prefix}"{s["path"]}" = {value}')
    return "\n".join(lines) + "\n"


__all__ = ["domains", "slot_catalog", "toml_template"]
