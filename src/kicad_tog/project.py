"""Which nets to check: given on the command line, or read from the KiCad project.

KiCad (6+) keeps net classes in the project file (``<board>.kicad_pro``, JSON),
not in the board file:

- ``net_settings.netclass_patterns``: wildcard patterns, e.g. ``/*PCIE*`` -> 90ohm;
- ``net_settings.netclass_assignments``: net name -> list of classes. The
  schematic's netclass directive labels end up here when it is saved.

Every class other than ``Default`` is treated as "the designer cares about this
net's return path" (in practice these are the impedance-controlled classes).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection, Sequence
from fnmatch import fnmatchcase
from pathlib import Path

DEFAULT_CLASS = "Default"
_WILDCARD = re.compile(r"[*?\[]")
_PAIR_SUFFIXES = (("_P", "_N"), ("+", "-"))


def is_pattern(name: str) -> bool:
    return bool(_WILDCARD.search(name))


def load_net_classes(project: Path) -> tuple[list[tuple[str, str]], dict[str, list[str]]]:
    """Return (patterns as (pattern, class), assignments as net -> classes).

    A missing project file gives no classes. Assignment values are lists in
    KiCad 8+ (a net may have several classes); a plain string is accepted too.
    """
    if not project.is_file():
        return [], {}
    settings = json.loads(project.read_text(encoding="utf-8")).get("net_settings") or {}

    patterns = [
        (entry["pattern"], entry["netclass"])
        for entry in settings.get("netclass_patterns") or []
        if entry.get("pattern") and entry.get("netclass")
    ]
    assignments = {
        net: [classes] if isinstance(classes, str) else list(classes)
        for net, classes in (settings.get("netclass_assignments") or {}).items()
    }
    return patterns, assignments


def classes_of(
    net: str,
    patterns: Sequence[tuple[str, str]],
    assignments: dict[str, list[str]],
) -> list[str]:
    """Every class a net belongs to, from assignments and from patterns."""
    classes = list(assignments.get(net, []))
    classes += [netclass for pattern, netclass in patterns if fnmatchcase(net, pattern)]
    return classes


def pair_nets(names: Collection[str]) -> list[str]:
    """Nets named like one half of a differential pair whose partner exists."""
    names = set(names)
    selected = []
    for name in names:
        for p, n in _PAIR_SUFFIXES:
            if name.endswith(p) and name[: -len(p)] + n in names:
                selected.append(name)
            elif name.endswith(n) and name[: -len(n)] + p in names:
                selected.append(name)
    return sorted(set(selected))


def select_nets(
    board_nets: Collection[str],
    requested: Sequence[str] | None,
    project: Path,
) -> tuple[list[str], str | None]:
    """Return (nets to check, why). ``why`` is None when the user named them.

    ``board_nets`` are the nets that have tracks. Precedence:
    1. ``requested`` (``--nets``): exact names, or wildcards like ``/*PCIE*``.
       An unknown name or a pattern matching nothing is an error: checking
       nothing would pass.
    2. Every net in a non-Default class of the project file.
    3. Pair names (``_P``/``_N``, ``+``/``-``) whose partner exists.
    4. Nothing: an error, never a silent pass.
    """
    board_nets = set(board_nets)

    if requested:
        selected: list[str] = []
        unknown = []
        for name in requested:
            if is_pattern(name):
                matches = sorted(net for net in board_nets if fnmatchcase(net, name))
                if not matches:
                    raise ValueError(f"no net matches {name}")
                selected += matches
            elif name in board_nets:
                selected.append(name)
            else:
                unknown.append(name)
        if unknown:
            raise ValueError(f"unknown net(s): {', '.join(sorted(unknown))}")
        return list(dict.fromkeys(selected)), None

    patterns, assignments = load_net_classes(project)
    per_class: Counter[str] = Counter()
    selected = []
    for net in sorted(board_nets):
        special = sorted({c for c in classes_of(net, patterns, assignments) if c != DEFAULT_CLASS})
        if special:
            selected.append(net)
            per_class.update(special)
    if selected:
        counts = ", ".join(f"{name}: {count}" for name, count in sorted(per_class.items()))
        return selected, f"net classes: {counts}"

    pairs = pair_nets(board_nets)
    if pairs:
        return pairs, "pair names, no net classes"

    raise ValueError("no nets selected: pass --nets or define net classes")
