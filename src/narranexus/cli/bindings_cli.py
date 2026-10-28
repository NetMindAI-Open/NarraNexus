"""
@file_name: bindings_cli.py
@author: Bin Liang
@date: 2026-09-07
@description: ``narranexus slots`` (the catalog: every slot by domain with candidates and what is bound, from which layer) and ``narranexus bind / unbind`` (edit the ``[bindings]`` table of ``<plugin home>/narranexus.toml`` with validation against the slot tree, the candidates and the distribution-only rule). Boots the builtins and the user registry on PRIVATE registries so it never touches a running host.
"""
from __future__ import annotations

import json

import tomllib
from pathlib import Path
from typing import Any

from narranexus.contracts import BindingConflict, UnknownEntry
from narranexus.kernel.plugins.catalog import slot_catalog, toml_template


def booted_registries(*, host_version: str | None = None) -> Any:
    """Private registries with every builtin (and enabled user plugin) loaded and the bindings resolved."""
    from narranexus.hosts.boot import boot
    from narranexus.kernel.plugins.compat import host_version as _hv
    from narranexus.kernel.plugins.distribution import resolve_from_env
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.platform.bindings_runtime import resolve_runtime_bindings
    regs = Registries()
    hv = host_version or _hv()
    res = resolve_from_env(host_version=hv)
    # inspect=True: exactly a backend boot minus every write-back — no boot
    # marker (three `narranexus slots` runs used to push the app into SAFE
    # MODE), no crash counts, no state transitions in registry.json.
    boot("backend", registries=regs, cloud=False, host_version=hv, distribution=res, inspect=True)
    resolve_runtime_bindings(res, registries=regs, snapshot=False)
    return regs


def render_catalog(catalog: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for group in catalog:
        out.append(f"\n{group['title']}")
        for s in group["slots"]:
            b = s["bound"]
            if s["arity"] == "one":
                bound = f"{b.get('provider')} [{b.get('layer')}{(' · ' + b['origin']) if b.get('origin') else ''}]"
            else:
                bound = ("order: " + ", ".join(b["providers"])) if b.get("providers") else f"all ({len(s['contributions'])})"
            flag = " distribution-only" if s["distribution_only"] else ""
            out.append(f"  {s['path']:<42} {s['arity']:<4} → {bound}{flag}")
            if s["candidates"]:
                out.append(f"  {'':<42}      candidates: {', '.join(s['candidates'])}")
    return "\n".join(out).strip("\n")


# ---------------------------------------------------------------- narranexus.toml editing


def read_bindings_table(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    table = data.get("bindings", {})
    return dict(table) if isinstance(table, dict) else {}


def _toml_value(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def write_bindings_table(path: Path, table: dict[str, Any]) -> None:
    """Rewrite only the ``[bindings]`` table; other tables of the file are kept verbatim."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.split("\n") if text else []
    out: list[str] = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        if line.strip() == "[bindings]":
            replaced = True
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("["):
                i += 1
            out.append("[bindings]")
            out.extend(f'"{k}" = {_toml_value(v)}' for k, v in table.items())
            out.append("")
            continue
        out.append(line)
        i += 1
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append("[bindings]")
        out.extend(f'"{k}" = {_toml_value(v)}' for k, v in table.items())
        out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")


def _names_a_candidate(value: str, entries: list[Any]) -> bool:
    """Every token of a binding value must name a registered entry: ``owner`` (any entry of
    that owner), ``name`` or ``owner:name`` (that entry), optionally verb-prefixed
    (``+``/``-``/``=``; a bare verb with no payload is an error). A token naming a real
    owner's NON-existent contribution used to pass (only the owner was checked) and the
    written binding then silently matched nothing."""
    for part in value.split(","):
        token = part.strip()
        while token and token[0] in "+-=":
            token = token[1:]
        if not token:
            return False
        owner, _, name = token.partition(":")
        if name:
            ok = any(e.owner == owner and e.name == name for e in entries)
        else:
            ok = any(e.owner == token or e.name == token for e in entries)
        if not ok:
            return False
    return True


def bind(regs: Any, path: Path, slot: str, providers: list[str]) -> dict[str, Any]:
    """Validate and write one binding; returns what the resolved bindings say afterwards."""
    from narranexus.kernel.plugins.bindings import parse_toml, resolve

    tree = regs.slots
    if slot not in tree:
        raise UnknownEntry(f"unknown slot {slot!r}; `narranexus slots` lists them")
    spec = tree.get(slot)
    if spec.distribution_only:
        raise BindingConflict(f"{slot} is distribution-only: bind it in narranexus-dist.json, not in narranexus.toml")
    entries = list(regs.registry_for(slot).entries())
    unknown = [p for p in providers if not _names_a_candidate(p, entries)]
    if unknown:
        raise UnknownEntry(f"{slot}: {unknown} registered nothing here (candidates: {sorted({e.owner for e in entries})})")
    if spec.arity == "one" and len(providers) != 1:
        raise BindingConflict(f"{slot} is one-arity: give exactly one provider")
    table = read_bindings_table(path)
    table[slot] = providers[0] if spec.arity == "one" else providers
    write_bindings_table(path, table)
    # prove the file still resolves against the BOOTED tree (a user plugin's
    # declared slots included — the builtin-only tree rejected them, which
    # made every later bind fail once such a binding existed); a conflict is
    # a real error, so the write is undone. Non-strict: a defaultless slot
    # nobody binds is not this binding's problem.
    try:
        resolve(tree, [parse_toml(path.read_text(encoding="utf-8"), origin=str(path))], strict=False)
    except Exception:
        table.pop(slot, None)
        write_bindings_table(path, table)
        raise
    return {"slot": slot, "bound": table[slot], "file": str(path)}


def unbind(path: Path, slot: str) -> bool:
    table = read_bindings_table(path)
    if slot not in table:
        return False
    table.pop(slot)
    write_bindings_table(path, table)
    return True


__all__ = ["bind", "booted_registries", "read_bindings_table", "render_catalog", "slot_catalog", "toml_template", "unbind", "write_bindings_table"]
