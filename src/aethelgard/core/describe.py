"""Tooltip text built from the data (GDD 12.2): what a skill does, with its numbers now and at the next level.

Pure Python, no pygame: the client only paints the lines, choosing font and color by ``style``.
Labels and formats come from ``data/stats.json``, so the same ``format_value``/``stat_line`` will
serve item tooltips (M6-04) without a second formatter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .data import GameData, StatDef
from .progression import Allocate, SkillTrees

EPS = 1e-9


@dataclass(frozen=True)
class Line:
    text: str
    style: str = "body"  # title | subtitle | body | header | stat | next | good | warn | muted


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v - round(v)) > EPS else str(round(v))


def format_value(stat: StatDef, value: float, signed: bool | None = None, exact: bool = False) -> str:
    """0.15 as a ``pct`` stat → "+15%". ``count`` rounds down unless ``exact`` (per-point synergy values)."""
    sign = stat.signed if signed is None else signed
    f = stat.format
    if f == "pct":
        text = f"{_num(value * 100)}%"
    elif f == "seconds":
        text = f"{_num(value)} s"
    elif f == "degrees":
        text = f"{_num(value)}°"
    elif f == "tiles":
        text = f"{_num(value)} tile{'' if abs(value - 1) < EPS else 's'}"
    elif f == "count" and not exact:
        text = str(math.floor(value + EPS))
    else:
        text = _num(value)
    return f"+{text}" if sign and value >= 0 else text


def stat_line(stat: StatDef, value: float) -> str:
    """Signed stats read "+15% damage"; the others "Duration: 3.4 s"."""
    if stat.signed:
        return f"{format_value(stat, value)} {stat.label}"
    return f"{stat.label[:1].upper()}{stat.label[1:]}: {format_value(stat, value)}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def describe_skill(data: GameData, trees: SkillTrees, node_id: str, implemented: bool = True) -> list[Line]:
    """Everything the player needs to decide on a point: effect in words, numbers now and next, cost, synergies."""
    n = data.node(node_id)
    prof = data.proficiency(n.tree)
    pts = trees.points_in(node_id)
    stats = [(sid, data.stat(sid)) for sid in trees.stats_of(node_id)]

    kind = "Active skill" if n.kind == "active" else "Passive"
    lines = [Line(n.name, "title"), Line(f"{kind}  ·  {prof.name} tree  ·  unlocks at {prof.name} level {n.layer}", "subtitle")]

    shown = max(1, pts)  # an unlearned skill is described at its first level
    words = {sid: format_value(st, trees.effect_at(node_id, sid, shown), signed=False) for sid, st in stats}
    lines.append(Line(n.description.format_map(words), "body"))
    if not implemented:
        lines.append(Line("Not in the prototype yet: points can be spent, the effect comes later.", "warn"))

    if n.kind == "active":
        if n.channeled:
            cost = f"Energy: {n.energy_cost:g} per second (hold the key)"
        else:
            gcd = data.balance.timing.global_cooldown_seconds
            cost = f"Energy: {n.energy_cost:g}  ·  Cooldown: " + (f"{n.cooldown:g} s" if n.cooldown > 0 else f"only the global {gcd:g} s")
        lines.append(Line(cost, "stat"))

    lines.append(Line(f"Points: {pts}/{n.max_points}", "header"))
    visible = [(sid, st) for sid, st in stats if st.format != "flag"]
    if pts > 0:
        lines.append(Line(f"Current level ({pts})", "header"))
        lines += [Line(stat_line(st, trees.effect_at(node_id, sid, pts)), "stat") for sid, st in visible]
    if pts < n.max_points:
        lines.append(Line(f"Next level ({pts + 1})", "header"))
        for sid, st in visible:
            now, nxt = trees.effect_at(node_id, sid, pts), trees.effect_at(node_id, sid, pts + 1)
            if abs(now) < EPS and abs(nxt) < EPS:
                continue
            delta = f"   ({format_value(st, nxt - now, signed=True, exact=True)})" if pts > 0 and abs(nxt - now) > EPS else ""
            lines.append(Line(stat_line(st, nxt) + delta, "next"))
        if pts + 1 > data.skills.diminishing_after:
            lines.append(Line(f"From point {data.skills.diminishing_after + 1} on, each point gives half.", "muted"))
    else:
        lines.append(Line("Maxed out.", "good"))

    syn = trees.synergies_into(node_id)
    if syn:
        lines.append(Line("Synergies", "header"))
        for s in syn:
            st = data.stat(s.stat)
            source = data.node(s.from_).name
            now = s.per_point * trees.points_in(s.from_)
            lines.append(Line(f"{format_value(st, s.per_point, signed=True, exact=True)} {st.label} per point in {source}"
                              f"  (now {format_value(st, now, signed=True, exact=True)})", "stat"))

    r = trees.can_allocate(node_id)
    if r is Allocate.LAYER_LOCKED:
        lines.append(Line(f"Requires {prof.name} level {n.layer} (now {trees.level_of(n.tree)}).", "warn"))
    elif r is Allocate.NO_POINTS:
        lines.append(Line(f"No free {prof.name} points.", "muted"))
    elif r is Allocate.OK:
        lines.append(Line(f"{_plural(trees.available(n.tree), f'free {prof.name} point')} to spend.", "good"))
    return lines
