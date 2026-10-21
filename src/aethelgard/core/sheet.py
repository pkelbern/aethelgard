"""Character sheet (C, GDD 12.2): every number the character fights with, and where it comes from.

Each row has a value and an explanation (``describe.Line``s) that the client shows when the mouse
is over it. Values are computed with the same functions the fight uses (``combat``, ``World``),
so the sheet can never disagree with what happens; "against a monster of your level" uses the
monster curves for the character's level.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .combat import player_attack, player_defense, player_guard
from .describe import Line


@dataclass
class Row:
    label: str
    value: str
    help: list[Line] = field(default_factory=list)


@dataclass
class Section:
    title: str
    rows: list[Row] = field(default_factory=list)


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%" if abs(v * 100 - round(v * 100)) < 0.05 else f"{v * 100:.1f}%"


def _num(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def character_sheet(world, player) -> list[Section]:
    s = player.session
    d = s.data
    c, ap = d.balance.character, d.balance.attributes
    sheet, a = s.character, s.character.attributes
    lvl = sheet.level
    mon = d.monster_level(max(1, lvl))
    cm = s.combat
    out: list[Section] = []

    # ---- who
    profs = sorted(s.proficiencies, key=lambda p: -p.level)
    used = [p for p in profs[1:] if p.level > 1]
    lvl_help = [
        Line("Character level", "title"),
        Line(f"Your best proficiency ({profs[0].name} {profs[0].level}) plus {_pct(c.secondary_proficiency_weight)} of the others "
             f"you have trained ({sum(p.level for p in used)}), rounded down. Proficiencies still at level 1 add nothing.", "body"),
        Line("It sets the level monsters compare against for hit chance and armor.", "muted"),
    ]
    title_help = [Line("Title", "title"),
                  Line("Named after your one or two highest proficiencies (the second counts when it is at least half the first).", "body")]
    who = Section(s.name, [Row("Level", str(lvl), lvl_help), Row("Title", sheet.title, title_help), Row("Gold", str(s.gold), [Line("Gold", "title"),
                  Line(f"Carried gold. Dying costs {_pct(d.balance.death.gold_loss_pct)} of it; the shared stash keeps gold safe.", "body")])])
    out.append(who)

    # ---- proficiencies
    sec = Section("Proficiencies")
    for p in s.proficiencies:
        nxt = d.xp_total_by_level[p.level] if p.level < len(d.xp_total_by_level) else None
        attrs = ", ".join(f"+{v} {d.stat(k).label}" for k, v in p.definition.attributes.items() if v)
        free = s.skills.available(p.id)
        help_ = [Line(f"{p.name} {p.level}", "title"), Line(p.definition.description or "Gains XP by being used.", "body"),
                 Line(f"Each level: {attrs} and 1 point in its tree.", "stat"),
                 Line(f"XP {p.xp:.0f}" + (f" / {nxt}  ({_pct(p.progress)} of this level)" if nxt else "  (maximum level)"), "next")]
        if free:
            help_.append(Line(f"{free} unspent point{'s' if free > 1 else ''} in the skill tree.", "good"))
        sec.rows.append(Row(p.name, f"{p.level}" + (f"  (+{free})" if free else ""), help_))
    out.append(sec)

    # ---- attributes
    sec = Section("Attributes")
    attr_help = {
        "str": f"+{_pct(ap.damage_per_str)} damage per point. Item requirements.",
        "dex": f"+{_num(ap.accuracy_per_dex)} accuracy, +{_pct(ap.crit_per_dex)} critical chance and +{_num(ap.defense_per_dex)} defense per point.",
        "vit": f"+{_num(c.life_per_vit)} life per point and +1 Energy per 2 points.",
        "int": f"+{_num(c.energy_per_int)} Energy and +{_pct(ap.spell_damage_per_int)} spell damage per point.",
    }
    for k in ("str", "dex", "vit", "int"):
        v = getattr(a, k)
        from_profs = v - getattr(sheet.base, k) - getattr(sheet.item_bonus, k)
        sec.rows.append(Row(d.stat(k).label, str(v), [Line(d.stat(k).label, "title"), Line(attr_help[k], "body"),
                        Line(f"{getattr(sheet.base, k)} to start, +{from_profs} from proficiency levels", "muted")]))
    out.append(sec)

    # ---- life and energy
    sec = Section("Life and Energy")
    regen = s.skills.effect("recovery", "lifeRegenPerSecond")
    sec.rows.append(Row("Life", f"{player.life:.0f} / {sheet.max_life:.0f}", [Line("Life", "title"),
                    Line(f"{_num(c.base_life)} base + {_num(c.life_per_vit)} per Vitality ({a.vit}) "
                         f"+ {_num(c.life_per_level)} per character level ({lvl}).", "body"),
                    Line(f"Regenerates {regen:g} per second (Recovery); potions and level-ups heal too.", "muted")]))
    sec.rows.append(Row("Energy", f"{player.energy:.0f} / {sheet.max_energy:.0f}", [Line("Energy", "title"),
                    Line(f"{_num(c.base_energy)} base + {_num(c.energy_per_int)} per Intelligence + 1 per 2 Vitality. "
                         "Every skill and every paid block spends it.", "body"),
                    Line(f"Regenerates {_pct(c.energy_regen_pct_per_second)} of the maximum per second, "
                         f"{_pct(c.guard_energy_regen_pct_per_second)} with the guard up.", "muted")]))
    out.append(sec)

    # ---- offense
    sec = Section("Offense")
    gear = s.gear
    atk = player_attack(sheet, s.skills, gear)
    lo, hi = atk.dmg_min * (1 + atk.damage_bonus_pct), atk.dmg_max * (1 + atk.damage_bonus_pct)
    weapon = gear.main_hand.name if gear.main_hand else "bare hands"
    sec.rows.append(Row("Damage", f"{lo:.0f}–{hi:.0f}", [Line("Damage per hit", "title"),
                    Line(f"{weapon}: {_num(atk.dmg_min)}–{_num(atk.dmg_max)}, +{_pct(atk.damage_bonus_pct)} from Strength and passives.", "body"),
                    Line(f"Critical hits deal x{d.balance.combat.crit_multiplier:g}. Melee hits on archers and casters: "
                         f"+{_pct(d.balance.combat.melee_vs_ranged_damage_pct)}.", "muted")]))
    if gear.dual_wielding:
        off = player_attack(sheet, s.skills, gear, None, "off")
        mult = off.dmg_max / gear.off_hand.base.dmg_max if gear.off_hand.base.dmg_max else 0
        sec.rows.append(Row("Off-hand damage", f"{off.dmg_min * (1 + off.damage_bonus_pct):.0f}–{off.dmg_max * (1 + off.damage_bonus_pct):.0f}",
                        [Line("Off-hand damage", "title"), Line(f"{gear.off_hand.name} at {_pct(mult)} of its damage (Off-Hand Training raises it). "
                         "Swings alternate between the hands.", "body")]))
    ias = world.attack_speed(player)
    per_sec = (1 + ias) / d.balance.timing.attack_seconds
    hit = cm.hit_chance(atk.accuracy, mon.defense, lvl, mon.level)
    per_hit = cm.average_hit(atk)
    if gear.dual_wielding:  # swings alternate between the hands
        per_hit = (per_hit + cm.average_hit(off)) / 2
    dps = per_sec * hit * per_hit
    sec.rows.append(Row("Average DPS", f"{dps:.1f}", [Line("Average damage per second", "title"),
                    Line(f"{per_sec:.2f} attacks per second × {_num(per_hit)} average damage per hit × {_pct(hit)} chance to hit "
                         f"a level {mon.level} monster, with critical hits averaged in.", "body"),
                    Line(f"A swing takes {d.balance.timing.attack_seconds:g} s / (1 + {_pct(ias)} attack speed); "
                         "attack speed comes from items, Ambidexterity, Quick Draw and Frenzy stacks.", "muted"),
                    Line("Basic attacks only, before the monster's armor.", "muted")]))
    sec.rows.append(Row("Accuracy", f"{atk.accuracy:.0f}", [Line("Accuracy", "title"),
                    Line(f"{_num(ap.base_accuracy)} + {_num(ap.accuracy_per_dex)} per Dexterity ({a.dex}), raised by skills.", "body"),
                    Line(f"Chance to hit a level {mon.level} monster: {_pct(hit)} (always between 5% and 95%).", "next")]))
    sec.rows.append(Row("Critical chance", _pct(atk.crit_chance), [Line("Critical chance", "title"),
                    Line(f"{_pct(ap.crit_per_dex)} per Dexterity, plus Hunter's Mark with bows.", "body")]))
    from_int = a.int * ap.spell_damage_per_int
    fire = s.skills.stat_total("fireDamagePct")
    sec.rows.append(Row("Spell damage", f"+{_pct(from_int + fire)}", [Line("Spell damage", "title"),
                    Line(f"{_pct(ap.spell_damage_per_int)} per Intelligence ({a.int}): +{_pct(from_int)}. "
                         f"Fire spells: +{_pct(fire)} more from Kindling.", "body"),
                    Line("Spells always hit and ignore armor; the enemy's resistance to the element takes its share. "
                         "Learn spells from wands (hover a spell in the skill trees).", "muted")]))
    reach = world.player_reach(player)
    sec.rows.append(Row("Reach", f"{reach:.1f} tiles", [Line("Reach", "title"),
                    Line("How far you strike from: a bow's range (Eagle Eye adds to it), or arm's length in melee, "
                         "measured to the edge of the target's body.", "body")]))
    out.append(sec)

    # ---- defense
    sec = Section("Defense")
    df = player_defense(sheet, s.skills, gear)
    dodge = 1 - cm.hit_chance(mon.accuracy, df.defense, mon.level, lvl)
    sec.rows.append(Row("Defense", f"{df.defense:.0f}", [Line("Defense", "title"),
                    Line(f"{_num(ap.defense_per_dex)} per Dexterity. It makes enemies miss.", "body"),
                    Line(f"A level {mon.level} monster misses you {_pct(dodge)} of the time (never with your guard up and facing it).", "next")]))
    red = cm.armor_reduction(df.armor, mon.level)
    sec.rows.append(Row("Armor", f"{df.armor:.0f}", [Line("Armor", "title"),
                    Line(f"Sum of your gear's armor, raised by Iron Skin (and Heavy Shield for the shield). It takes a share off every physical hit, "
                         f"at most {_pct(d.balance.combat.armor_cap)}.", "body"),
                    Line(f"Against a level {mon.level} monster: {_pct(red)} less damage. Higher-level attackers get through more.", "next")]))
    g = player_guard(s.skills, gear, d.balance.guard)
    if g.can_guard:
        sec.rows.append(Row("Block", _pct(g.block_reduction), [Line("Guard (hold Space)", "title"),
                        Line(f"A blocked hit loses {_pct(g.block_reduction)} of its damage (at most {_pct(d.balance.guard.block_reduction_cap)}); "
                             "armor works on the rest.", "body"),
                        Line(f"Covers {g.arc_degrees:.0f}° in front. Each block costs Energy, {_pct(g.efficiency)} less with guard efficiency. "
                             f"Raising the guard just in time ({g.perfect_window:.2f} s) is a perfect block.", "muted")]))
    else:
        why = "a bow takes both hands" if gear.has_bow else "two weapons" if gear.dual_wielding else "no shield"
        help_ = [Line("Guard", "title"), Line(f"No guard: {why}.", "body")]
        deflect = s.skills.effect("blade_guard", "deflectChance") if gear.dual_wielding else 0
        if deflect:
            help_.append(Line(f"Blade Guard deflects {_pct(deflect)} of melee hits instead.", "good"))
        sec.rows.append(Row("Block", "—", help_))
    out.append(sec)

    # ---- movement
    mv = d.balance.movement.player_tiles_per_second
    out.append(Section("Movement", [Row("Speed", f"{mv:g} tiles/s", [Line("Movement speed", "title"),
               Line(f"Half as fast with the guard up ({_pct(d.balance.guard.move_speed_mult)}).", "body")])]))
    return out

