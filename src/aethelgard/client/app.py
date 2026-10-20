"""pygame-ce client: window, input, drawing. It only sends commands to the world (core)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import pygame

from ..core.data import GameData
from ..core.describe import Line, describe_skill
from ..core.events import ExitReached, FloatingText, LootPicked, PlayerDied, ProficiencyLeveledUp
from ..core.items import GoldDrop, ItemDrop, PotionDrop, ScrollDrop
from ..core.progression import Bind
from ..core.saves import SaveStore
from ..core.session import GameSession
from ..core.simulation import run_act
from ..core.world import AttackTarget, MonsterActor, MoveTo, SetGuard, UseSkill, World, is_implemented
from ..core.world_gen import ExitKind
from . import palette as C
from .hud import Hud
from .iso import Camera, MapRenderer
from .keys import KeyMap
from .tooltip import draw_tooltip
from .ui import GameUI
from .vision import Vision

FPS = 60
DT = 1 / FPS
MONSTER_RADIUS = {"boss": 22, "unique": 15, "champion": 13, "berserker": 13}  # body size on screen, by rank (default 10)
CONSOLE_HELP = (
    "save · load · bag · xp <prof> <amount> · level <prof> <n> · point <node> · area <id> [floor] · "
    "god · kill · heal · reveal · sim · seed"
)


@dataclass
class Floater:
    gx: float
    gy: float
    text: str
    color: tuple
    life: float = 0.9


class Game:
    """``new_game`` ignores an existing save; ``save_dir`` overrides the per-user save folder."""

    def __init__(self, seed: int | None = None, size: tuple[int, int] = (1280, 720), new_game: bool = False, save_dir: Path | None = None):
        pygame.init()
        pygame.display.set_caption("Aethelgard")
        self.screen = pygame.display.set_mode(size)
        self.clock = pygame.time.Clock()
        self.fonts = {
            "title": pygame.font.Font(None, 34),
            "body": pygame.font.Font(None, 24),
            "small": pygame.font.Font(None, 20),
            "tiny": pygame.font.Font(None, 17),
        }
        self.data = GameData.load()
        self.seed = seed if seed is not None else time.time_ns() & 0xFFFFFFFF
        self.store = SaveStore(save_dir)
        self.keys = KeyMap.load(self.data.skills.bar_slots)
        self.cam = Camera(*size)
        self.hud = Hud(self.fonts, self.keys)
        self.hovered: MonsterActor | None = None
        self.vision = Vision()
        self.big_map = False
        self.floaters: list[Floater] = []
        self.map: MapRenderer | None = None
        self.area_names = {a.id: a.name for a in self.data.all_areas}
        self.running = True

        loaded = None if new_game else self._read_save()
        self._bind(loaded or GameSession(self.data, self.seed))
        self.ui = GameUI(self, size)
        self.load_area(self.data.town_id)
        if loaded:
            self.hud.add(f"Welcome back, {loaded.name}. Save loaded.", C.GOLD)
        self.hud.add(f"World seed: {self.seed}", C.MUTED)

    # ------------------------------------------------------------ session and save

    def _bind(self, session: GameSession) -> None:
        """Builds the world around a character and listens to its events (new game or loaded save)."""
        self.session = session
        self.world = World([session])
        self.player = self.world.players[session.player_id]
        bus = session.bus
        bus.subscribe(FloatingText, lambda e: self.floaters.append(Floater(e.x, e.y, e.text, C.FLOAT.get(e.kind, C.TEXT))))
        bus.subscribe(ProficiencyLeveledUp, self._on_level_up)
        bus.subscribe(LootPicked, self._on_loot)
        bus.subscribe(ExitReached, self._on_exit)
        bus.subscribe(PlayerDied, self._on_death)

    def _read_save(self) -> GameSession | None:
        try:
            return self.store.load_session(self.data, new_world_seed=self.seed)
        except ValueError as ex:
            print(f"Could not load the save ({ex}); starting a new character.")
            return None

    def save_game(self) -> str:
        path = self.store.save_session(self.session)
        self.hud.add("Game saved.", C.GOLD)
        return str(path)

    def load_game(self) -> str:
        """Reloads the save in a fresh world and goes back to town (GDD 9.4)."""
        loaded = self._read_save()
        if loaded is None:
            return "no save to load"
        self._bind(loaded)
        self.load_area(self.data.town_id)
        self.hud.add(f"Save loaded: {loaded.name}.", C.GOLD)
        return "loaded"

    # ------------------------------------------------------------ core events

    def _on_level_up(self, e: ProficiencyLeveledUp) -> None:
        name = self.session.proficiencies[e.proficiency_id].name
        self.hud.add(f"{name} reached level {e.new_level}! Tree point available.", C.GOLD)

    def _on_loot(self, e: LootPicked) -> None:
        d = e.drop
        if isinstance(d, ItemDrop):
            text = f"{d.item.name}  ({d.item.base.name}, iLvl {d.item.item_level})"
            self.hud.add(text if e.stored else f"Backpack full, left behind: {text}", C.RARITY[int(d.item.rarity)] if e.stored else C.MUTED)
        elif isinstance(d, PotionDrop):
            self.hud.add(f"{d.kind.capitalize()} Potion", C.MUTED)
        elif isinstance(d, ScrollDrop):
            self.hud.add(f"{d.kind.capitalize()} Scroll", C.MUTED)
        elif isinstance(d, GoldDrop):
            self.hud.add(f"+{d.amount} gold", C.GOLD)

    def _on_exit(self, e: ExitReached) -> None:
        kind = ExitKind(e.kind)
        w = self.world
        if kind == ExitKind.STAIRS_DOWN:
            self.load_area(w.area_id, w.floor + 1, arrive_at=ExitKind.STAIRS_UP)
        elif kind == ExitKind.STAIRS_UP:
            self.load_area(w.area_id, w.floor - 1, arrive_at=ExitKind.STAIRS_DOWN)
        elif e.target_area:
            self.load_area(e.target_area, 0, came_from=w.area_id)

    def _on_death(self, e: PlayerDied) -> None:
        r = self.session.apply_death()
        extra = "".join(f", {v:.0f} {self.session.proficiencies[k].name} XP" for k, v in r.xp_lost.items())
        self.hud.add(f"You died. Lost {r.gold_lost} gold{extra}.", C.LIFE)
        self.player.reset_vitals()
        self.load_area(self.data.town_id)

    def load_area(self, area_id: str, floor: int = 0, came_from: str | None = None, arrive_at: ExitKind | None = None) -> None:
        self.world.enter_area(area_id, floor, came_from, arrive_at)
        area = self.data.area(area_id)
        self.map = MapRenderer(self.world.layout, area.kind)
        self.vision.enter((area_id, floor), self.world.layout, reveal_all=area.kind == "town")
        self.vision.update(self.player.x, self.player.y)
        suffix = f" – floor {floor + 1}" if area.kind == "dungeon" else ""
        level = "" if area.kind == "town" else f"  (level {area.level_for_floor(floor)})"
        self.hud.area_title = f"{area.name}{suffix}{level}"
        self.floaters.clear()
        self.cam.follow(self.player.x, self.player.y, DT, snap=True)

    # ------------------------------------------------------------ input

    def mouse_grid(self) -> tuple[float, float]:
        mx, my = pygame.mouse.get_pos()
        return self.cam.to_grid(mx, my)

    def monster_under_mouse(self) -> MonsterActor | None:
        """Visible monster whose body is under the cursor (a little margin around it), the closest one first."""
        mx, my = pygame.mouse.get_pos()
        best, best_d = None, math.inf
        for m in self.world.monsters.values():
            if not self.vision.sees(m.x, m.y):
                continue
            r = MONSTER_RADIUS.get(m.monster.rank.id, 10)
            sx, sy = self.cam.to_screen(m.x, m.y)
            d = math.hypot(sx - mx, sy - r - my)
            if d <= r + 12 and d < best_d:
                best, best_d = m, d
        return best

    def slot_under_mouse(self) -> int | None:
        return self.hud.slot_at(pygame.mouse.get_pos(), len(self.session.skill_bar), self.screen.get_size())

    def _update_hover(self) -> None:
        over_hud = self.ui.over_ui or self.slot_under_mouse() is not None
        self.hovered = None if over_hud else self.monster_under_mouse()

    # ------------------------------------------------------------ action bar

    def bind_slot(self, slot: int, node_id: str | None) -> None:
        """Puts a skill on a bar key (None clears the key). Messages say why when it cannot."""
        bar, label = self.session.skill_bar, self.keys.slot_label(slot)
        if node_id is None:
            bar.clear(slot)
            self.hud.add(f"Key {label} cleared.", C.MUTED)
        else:
            name = self.data.node(node_id).name
            r = bar.bind(slot, node_id)
            self.hud.add(f"{name} on key {label}." if r is Bind.OK else f"{name}: {r.value}", C.TEXT if r is Bind.OK else C.MUTED)
        self.ui.refresh_tree_if_open()

    def use_slot(self, slot: int) -> None:
        skill = self.session.skill_bar.skill_at(slot)
        if skill is None:
            self.hud.add(f"Key {self.keys.slot_label(slot)} is empty: click the slot to choose a skill.", C.MUTED)
            return
        target = self.hovered or self.nearest_monster()
        gx, gy = self.mouse_grid()
        msg = self.world.apply(UseSkill(self.session.player_id, skill, target.id if target else None, gx, gy))
        if msg:
            self.hud.add(msg, C.MUTED)

    def skill_tooltip(self) -> list[Line] | None:
        """Lines for the skill under the mouse: a node in the tree window or a slot of the bar."""
        pos = pygame.mouse.get_pos()
        bar, keys = self.session.skill_bar, self.keys
        node_id = self.ui.hovered_node(pos)
        if node_id is not None:
            return self._skill_lines(node_id, f"Hover and press {keys.bar_labels} to put it on that key.")
        slot = None if self.ui.over_ui else self.slot_under_mouse()
        if slot is None:
            return None
        node_id = bar.skill_at(slot)
        if node_id is None:
            return [Line(f"Key {keys.slot_label(slot)}: empty", "title"), Line("Click to choose a skill for this key.", "hint")]
        return self._skill_lines(node_id, "Click: choose another skill  ·  Right-click: clear this key")

    def _skill_lines(self, node_id: str, hint: str) -> list[Line]:
        lines = describe_skill(self.data, self.session.skills, node_id, is_implemented(node_id))
        if self.data.node(node_id).kind == "active":
            slot = self.session.skill_bar.slot_of(node_id)
            lines.append(Line(f"On the bar: key {self.keys.slot_label(slot)}" if slot is not None else "Not on the bar.", "hint"))
            lines.append(Line(hint, "hint"))
        return lines

    def nearest_monster(self, max_tiles: float = 3.0) -> MonsterActor | None:
        p = self.player
        cands = [m for m in self.world.monsters.values() if math.hypot(m.x - p.x, m.y - p.y) <= max_tiles]
        return min(cands, key=lambda m: math.hypot(m.x - p.x, m.y - p.y)) if cands else None

    def handle_events(self) -> None:
        pid = self.session.player_id
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
                continue
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_F1:
                self.ui.toggle_console()
                continue
            if self.ui.process(ev) or self.ui.typing:
                continue  # the interface (windows, text field) consumed the event
            if ev.type == pygame.MOUSEBUTTONDOWN:
                self._on_click(ev.button)
            elif ev.type == pygame.KEYDOWN:
                self._on_key(ev.key)

        # guard: hold the guard key, shield pointed at the mouse
        held = not self.ui.typing and pygame.key.get_pressed()[self.keys.key("guard")]
        gx, gy = self.mouse_grid()
        self.world.apply(SetGuard(pid, bool(held), gx - self.player.x, gy - self.player.y))

    def _on_click(self, button: int) -> None:
        slot = self.slot_under_mouse()
        if slot is not None:  # the bar: left picks a skill for the key, right clears it
            if button == 1:
                self.ui.open_picker(slot)
            elif button == 3:
                self.bind_slot(slot, None)
            return
        if button != 1:
            return
        pid = self.session.player_id
        target = self.hovered
        if target is not None and not self.player.guarding:
            self.world.apply(AttackTarget(pid, target.id))
        else:
            self.world.apply(MoveTo(pid, *self.mouse_grid()))

    def _on_key(self, key: int) -> None:
        slot = self.keys.slot_for(key)
        if slot is not None:
            node_id = self.ui.hovered_node(pygame.mouse.get_pos())
            if node_id is not None:  # D2 style: hover a skill in the trees and press the key to bind it
                self.bind_slot(slot, node_id)
            else:
                self.use_slot(slot)
            return
        action = self.keys.action_for(key)
        if action == "save":
            self.save_game()
        elif action == "skillTrees":
            self.ui.toggle_tree()
        elif action == "map":
            self.big_map = not self.big_map
        elif action == "cancel":
            if self.ui.any_open:
                self.ui.close_all()
            else:
                self.running = False

    # ------------------------------------------------------------ console

    def command(self, name: str, args: list[str]) -> str:
        s, w = self.session, self.world
        if name == "help":
            return CONSOLE_HELP
        if name == "save":
            return f"saved to {self.save_game()}"
        if name == "load":
            return self.load_game()
        if name == "bag":
            gear = ", ".join(f"{slot}: {i.name}" for slot, i in s.gear.slots.items())
            bag = "\n".join(f"  {i.name} ({i.base.name}, iLvl {i.item_level})" for i in s.inventory) or "  (empty)"
            potions = ", ".join(f"{k} ×{v}" for k, v in s.consumables.items() if v) or "none"
            return f"Gold {s.gold}\nEquipped: {gear}\nBackpack {len(s.inventory)}/{s.inventory.capacity}:\n{bag}\nConsumables: {potions}"
        if name == "xp":
            p = s.proficiencies[args[0]]
            p.restore(p.xp + float(args[1]))
            return f"{p.name}: level {p.level}"
        if name == "level":
            p = s.proficiencies[args[0]]
            p.restore(self.data.xp_total_by_level[int(args[1]) - 1])
            self.player.reset_vitals()
            return f"{p.name}: level {p.level}"
        if name == "point":
            r = s.allocate_skill(args[0]).value
            self.ui.refresh_tree_if_open()
            return r
        if name == "area":
            self.load_area(args[0], int(args[1]) - 1 if len(args) > 1 else 0)
            return "ok"
        if name == "god":
            self.player.god_mode = not self.player.god_mode
            return "invulnerable" if self.player.god_mode else "normal"
        if name == "kill":
            return f"{w.kill_all(self.player)} monsters"
        if name == "heal":
            self.player.reset_vitals()
            return "ok"
        if name == "sim":
            return "\n".join(f"{x.label}: B{x.main_level} S{x.shield_level} A{x.armor_level}" for x in run_act(self.data))
        if name == "reveal":
            self.vision.explored[:] = True
            return "map revealed"
        if name == "seed":
            return str(self.seed)
        return f"unknown command: {name}"

    # ------------------------------------------------------------ drawing

    def draw(self) -> None:
        screen, cam = self.screen, self.cam
        screen.fill(C.BG)
        self.map.draw(screen, cam, self.vision.explored)
        self.map.draw_markers(screen, cam, self.fonts["tiny"], self.area_names)

        # monsters outside the field of view are not drawn
        actors = [(m.x + m.y, "m", m) for m in self.world.monsters.values() if self.vision.sees(m.x, m.y)]
        actors.append((self.player.x + self.player.y, "p", self.player))
        for _, kind, a in sorted(actors, key=lambda t: t[0]):
            if kind == "m":
                self._draw_monster(a)
            else:
                self._draw_player(a)

        for f in self.floaters:
            sx, sy = cam.to_screen(f.gx, f.gy)
            img = self.fonts["small"].render(f.text, True, f.color)
            img.set_alpha(int(255 * max(0, min(1, f.life / 0.9))))
            screen.blit(img, (sx - img.get_width() // 2, sy - 56 - (0.9 - f.life) * 45))

        self.vision.draw_automap(screen, self.world.layout, self.player.x, self.player.y, big=self.big_map)
        self.hud.draw(screen, self.world, self.player, self.hovered)
        self.ui.draw(screen)
        tip = self.skill_tooltip()
        if tip:
            draw_tooltip(screen, self.fonts, tip, pygame.mouse.get_pos())
        pygame.display.flip()

    def _draw_player(self, p) -> None:
        sx, sy = self.cam.to_screen(p.x, p.y)
        screen = self.screen
        pygame.draw.ellipse(screen, (0, 0, 0), (sx - 12, sy - 5, 24, 10))
        body = C.PLAYER_BODY if p.state not in ("hit_recovery", "stunned") else (200, 80, 80)
        pygame.draw.rect(screen, body, (sx - 9, sy - 40, 18, 34), border_radius=4)
        pygame.draw.circle(screen, C.PLAYER_HEAD, (sx, sy - 46), 8)
        # facing direction, in screen space
        fx, fy = self.cam.to_screen(p.x + p.face_x, p.y + p.face_y)
        dx, dy = fx - sx, fy - sy
        n = math.hypot(dx, dy) or 1
        ux, uy = dx / n, dy / n
        if p.session.gear.has_shield:
            if p.guarding:
                pygame.draw.circle(screen, C.SHIELD, (sx + ux * 16, sy - 26 + uy * 16), 10)
                ang = math.atan2(uy, ux)
                arc = math.radians(p.session.data.balance.guard.arc_degrees) / 2
                if p.bulwark_left > 0:
                    pygame.draw.circle(screen, C.GUARD, (sx, sy - 20), 36, 2)
                else:
                    pygame.draw.arc(screen, C.GUARD, (sx - 36, sy - 56, 72, 72), -ang - arc, -ang + arc, 2)
            else:
                pygame.draw.rect(screen, C.SHIELD, (sx - 17, sy - 32, 8, 16))
        swing = 0.0
        if p.state == "attacking" and p.attack_duration > 0:
            swing = math.sin(math.pi * min(1, p.attack_elapsed / p.attack_duration))
        tip = (sx + 10 + ux * (14 + 14 * swing), sy - 30 + uy * (14 + 14 * swing) - 16 * (1 - swing))
        pygame.draw.line(screen, C.SWORD, (sx + 8, sy - 28), tip, 3)

    def _draw_monster(self, m: MonsterActor) -> None:
        sx, sy = self.cam.to_screen(m.x, m.y)
        rank = m.monster.rank.id
        r = MONSTER_RADIUS.get(rank, 10)
        color = C.RANK.get(rank, C.RANK["normal"])
        name_color = C.rank_name_color(rank)
        if m.state in ("hit_recovery", "stunned"):
            color = tuple(min(255, c + 70) for c in color)
        screen = self.screen
        pygame.draw.ellipse(screen, (0, 0, 0), (sx - r, sy - r // 3, 2 * r, 2 * r // 3))
        if m is self.hovered:  # outline at the feet, in the color of its name
            pygame.draw.ellipse(screen, name_color, (sx - r - 4, sy - r // 3 - 2, 2 * r + 8, 2 * r // 3 + 4), 2)
        pygame.draw.circle(screen, color, (sx, sy - r), r)
        if m.state == "attacking":  # swing warning: a ring that closes until the impact
            k = min(1.0, m.attack_elapsed / (m.attack_duration * 0.5))
            pygame.draw.circle(screen, (255, 255, 255), (sx, sy - r), int(r + 10 * (1 - k)), 1)
        frac = m.monster.hp / m.monster.max_hp
        pygame.draw.rect(screen, (0, 0, 0), (sx - 16, sy - 2 * r - 10, 32, 4))
        pygame.draw.rect(screen, C.LIFE, (sx - 16, sy - 2 * r - 10, 32 * frac, 4))
        first_of_pack = not m.pack_ids or m.pack_ids[0] == m.id or m.pack_ids[0] not in self.world.monsters
        if m is self.hovered or rank in ("unique", "boss") or (rank in ("champion", "berserker") and first_of_pack):
            img = self.fonts["tiny"].render(m.monster.name, True, name_color)  # details go to the target frame
            screen.blit(img, (sx - img.get_width() // 2, sy - 2 * r - 26))

    # ------------------------------------------------------------ loop

    def tick(self) -> None:
        self.handle_events()
        self.world.step(DT)  # the world keeps running while windows are open
        self._update_hover()
        self.vision.update(self.player.x, self.player.y)
        self.ui.update(DT)
        for f in self.floaters:
            f.life -= DT
        self.floaters = [f for f in self.floaters if f.life > 0]
        self.cam.follow(self.player.x, self.player.y, DT)
        self.draw()

    def run(self, frames: int | None = None, screenshot: str | None = None) -> None:
        """Interactive runs save on exit; ``frames`` runs (tests/CI) never touch the save."""
        n = 0
        while self.running:
            self.tick()
            n += 1
            if frames is None:
                self.clock.tick(FPS)
            elif n >= frames:
                break
        if frames is None:
            self.store.save_session(self.session)
        if screenshot:
            pygame.image.save(self.screen, screenshot)
        pygame.quit()
