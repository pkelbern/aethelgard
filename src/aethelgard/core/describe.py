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


def describe_item(data: GameData, item, missing: dict[str, int] | None = None, gear=None) -> list[Line]:
    """Item tooltip: name in its rarity color, base and slot, damage or armor, guard numbers, bonus lines,
    the set it belongs to (bonuses lit by how many pieces ``gear`` wears), item level and requirements."""
    b = item.base
    rarity = item.rarity.name.title()
    sub = "  ·  ".join(x for x in (b.name if b.name != item.name else "", data.items.slot_names.get(b.slot, b.slot), rarity) if x)
    lines = [Line(item.name, f"rarity{int(item.rarity)}"), Line(sub, "subtitle")]
    if b.dmg_max > 0:
        lines.append(Line(f"Damage: {_num(b.dmg_min)}–{_num(b.dmg_max)}", "stat"))
        if b.two_handed:
            lines.append(Line("Two-handed", "stat"))
        if b.attack_speed_pct:
            lines.append(Line(stat_line(data.stat("attackSpeedPct"), b.attack_speed_pct), "stat"))
        trains = next((p.name for p in data.proficiencies.proficiencies if set(b.tags) & set(p.weapon_tags)), None)
        if trains:
            lines.append(Line(f"Trains {trains}", "muted"))
    if b.armor > 0:
        lines.append(Line(f"Armor: {_num(b.armor)}", "stat"))
    if "shield" in b.tags:
        if b.block_reduction > 0:
            lines.append(Line(stat_line(data.stat("blockReduction"), b.block_reduction), "stat"))
        if b.guard_efficiency > 0:
            lines.append(Line(stat_line(data.stat("guardEfficiency"), b.guard_efficiency), "stat"))
    if b.spell:
        spell = data.node(b.spell)
        school = data.proficiency(spell.tree).name
        lines.append(Line(f"+{b.spell_level} to {spell.name} while worn", "next"))
        lines.append(Line(f"Lets you cast {spell.name} before you unlock it in the {school} tree.", "muted"))
    lines += [Line(stat_line(data.stat(k), v), "next") for k, v in item.mods]
    if item.set_id:
        lines += _set_lines(data, data.item_set(item.set_id), gear.set_pieces_worn(item.set_id) if gear else 0)
    lines.append(Line(f"Item level {item.item_level}", "muted"))
    missing = missing or {}
    for k, v in b.req.items():
        if v > 0:
            lines.append(Line(f"Requires {v} {data.stat(k).label}", "warn" if k in missing else "muted"))
    return lines


def _set_lines(data: GameData, st, worn: int) -> list[Line]:
    """The set's name, its pieces and its partial bonuses; the ones already active stand out."""
    lines = [Line(f"{st.name} ({worn}/{len(st.pieces)})", "header"), Line(", ".join(p.name for p in st.pieces), "muted")]
    for b in st.bonuses:
        style = "good" if worn >= b.pieces else "muted"
        lines += [Line(f"({b.pieces} items) {stat_line(data.stat(k), v)}", style) for k, v in b.stats.items()]
    return lines


def item_numbers(item) -> dict[str, float]:
    """Every number of an item by stat id: base damage/armor/guard numbers plus its affixes (summed)."""
    b = item.base
    out: dict[str, float] = {}
    if b.dmg_max > 0:
        out["weaponDamage"] = (b.dmg_min + b.dmg_max) / 2
    if b.armor > 0:
        out["armor"] = b.armor
    if "shield" in b.tags:
        out["blockReduction"] = b.block_reduction
        out["guardEfficiency"] = b.guard_efficiency
    for k, v in item.mods:
        out[k] = out.get(k, 0.0) + v
    return out


def compare_items(data: GameData, item, equipped) -> list[Line]:
    """Shift in the inventory (GDD 12.2, M6-04): what changes if ``item`` replaces ``equipped``.
    Gains in green, losses in red; stats both share and that do not change are left out."""
    if equipped is None:
        return [Line("Nothing worn in this slot", "header")]
    lines = [Line(f"Instead of {equipped.name}", "header")]
    new, old = item_numbers(item), item_numbers(equipped)
    for sid in dict.fromkeys([*new, *old]):
        delta = new.get(sid, 0.0) - old.get(sid, 0.0)
        if abs(delta) > EPS:
            lines.append(Line(stat_line(data.stat(sid), delta), "good" if delta > 0 else "warn"))
    if len(lines) == 1:
        lines.append(Line("Same numbers", "muted"))
    return lines


def _active_status(data: GameData, trees: SkillTrees, node_id: str) -> list[Line]:
    """An active's level: unlocked or not, its XP by use, what a wand adds and the gates ahead."""
    n = data.node(node_id)
    prof = data.proficiency(n.tree).name
    own, granted = trees.own_level(node_id), trees.granted(node_id)
    out = []
    if node_id in trees.unlocked:
        extra = f"  (+{granted} from your wand)" if granted else ""
        out.append(Line(f"Level {own}{extra}  ·  {trees.xp_progress(node_id):.0%} to the next by use", "header"))
        out.append(Line("It grows only while you use it: each hit gives it XP.", "muted"))
    else:
        out.append(Line(f"Locked: 1 {prof} point unlocks it at level 1; then it levels up by use.", "warn"))
        if granted:
            out.append(Line(f"+{granted} from your wand: you can cast it while the wand is worn.", "stat"))
    held = trees.gate_holding(node_id)
    if held is not None:
        out.append(Line(f"Held at level {held.level - 1}: needs {held.points} points in {data.node(held.node).name} "
                        f"(now {trees.points.get(held.node, 0)}). Its XP keeps building.", "warn"))
    for g in trees.closed_gates(node_id):
        if g is not held and g.level > own:
            out.append(Line(f"Level {g.level} needs {g.points} points in {data.node(g.node).name}.", "muted"))
    return out


def describe_skill(data: GameData, trees: SkillTrees, node_id: str, implemented: bool = True) -> list[Line]:
    """Everything the player needs to decide on a point: effect in words, numbers now and next, cost, synergies."""
    n = data.node(node_id)
    prof = data.proficiency(n.tree)
    pts = trees.points_in(node_id)
    stats = [(sid, data.stat(sid)) for sid in trees.stats_of(node_id)]

    kind = "Spell" if n.spell else "Active skill" if n.kind == "active" else "Passive"
    lines = [Line(n.name, "title"), Line(f"{kind}  ·  {prof.name} tree  ·  unlocks at {prof.name} level {n.layer}", "subtitle")]

    shown = max(1, pts)  # a skill with no level yet is described at its first one
    words = {sid: format_value(st, trees.effect_at(node_id, sid, shown), signed=False) for sid, st in stats}
    lines.append(Line(n.description.format_map(words), "body"))
    if not implemented:
        lines.append(Line("Not in the prototype yet: points can be spent, the effect comes later.", "warn"))
    if n.spell:
        lines.append(Line(f"{n.spell.element.title()} damage: always hits and ignores armor; the enemy's {n.spell.element} resistance "
                          "takes its share. Intelligence raises it.", "muted"))

    if n.kind == "active":
        if n.channeled:
            cost = f"Energy: {n.energy_cost:g} per second (hold the key)"
        else:
            gcd = data.balance.timing.global_cooldown_seconds
            cost = f"Energy: {n.energy_cost:g}  ·  Cooldown: " + (f"{n.cooldown:g} s" if n.cooldown > 0 else f"only the global {gcd:g} s")
        lines.append(Line(cost, "stat"))

    active = n.kind == "active"
    if active:
        lines += _active_status(data, trees, node_id)
    else:
        lines.append(Line(f"Points: {pts}/{n.max_points}", "header"))
    visible = [(sid, st) for sid, st in stats if st.format != "flag"]
    if pts > 0:
        lines.append(Line(f"Current level ({pts})", "header"))
        lines += [Line(stat_line(st, trees.effect_at(node_id, sid, pts)), "stat") for sid, st in visible]
    if active or pts < n.max_points:
        lines.append(Line(f"Next level ({pts + 1})", "header"))
        for sid, st in visible:
            now, nxt = trees.effect_at(node_id, sid, pts), trees.effect_at(node_id, sid, pts + 1)
            if abs(now) < EPS and abs(nxt) < EPS:
                continue
            delta = f"   ({format_value(st, nxt - now, signed=True, exact=True)})" if pts > 0 and abs(nxt - now) > EPS else ""
            lines.append(Line(stat_line(st, nxt) + delta, "next"))
        if pts + 1 > data.skills.diminishing_after:
            lines.append(Line(f"From level {data.skills.diminishing_after + 1} on, each level gives half.", "muted"))
    else:
        lines.append(Line("Maxed out.", "good"))

    syn = trees.synergies_into(node_id)
    if syn:
        lines.append(Line("Synergies", "header"))
        for s in syn:
            st = data.stat(s.stat)
            source = data.node(s.from_).name
            now = s.per_point * trees.points_in(s.from_)
            lines.append(Line(f"{format_value(st, s.per_point, signed=True, exact=True)} {st.label} per level of {source}"
                              f"  (now {format_value(st, now, signed=True, exact=True)})", "stat"))

    r = trees.can_allocate(node_id)
    if r is Allocate.LAYER_LOCKED:
        lines.append(Line(f"Requires {prof.name} level {n.layer} (now {trees.level_of(n.tree)}).", "warn"))
    elif r is Allocate.REQUIRES:
        need = ", ".join(data.node(x).name for x in trees.missing_requirements(node_id))
        lines.append(Line(f"Take {need} first.", "warn"))
    elif r is Allocate.NO_POINTS:
        lines.append(Line(f"No free {prof.name} points.", "muted"))
    elif r is Allocate.OK:
        verb = "Click to unlock" if active else "Click to add a point"
        lines.append(Line(f"{verb}  ·  {_plural(trees.available(n.tree), f'free {prof.name} point')}.", "good"))
    return lines
