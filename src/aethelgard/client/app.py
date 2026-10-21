"""pygame-ce client: window, input, drawing. It only sends commands to the world (core)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path

import pygame

from ..core.data import GameData
from ..core.describe import Line, describe_skill
from ..core.events import (
    ExitReached,
    FloatingText,
    LootPicked,
    MonsterKilled,
    PlayerDied,
    ProficiencyLeveledUp,
    StashChanged,
    WaypointReached,
)
from ..core.items import GoldDrop, ItemDrop, drop_name
from ..core.progression import Bind
from ..core.saves import SaveStore
from ..core.session import GameSession
from ..core.simulation import run_act
from ..core.world import (
    AttackInPlace,
    AttackTarget,
    MonsterActor,
    MoveTo,
    OpenPortal,
    PickUp,
    Respawn,
    SetGuard,
    TravelWaypoint,
    UseConsumable,
    UseSkill,
    World,
    is_implemented,
)
from ..core.world_gen import ExitKind
from . import palette as C
from .hud import Hud
from .iso import Camera, MapRenderer
from .item_windows import InventoryWindow, ShopWindow, StashWindow
from .keys import CONSUMABLE_KEYS, KeyMap
from .loot_view import GroundView
from .sprites import ActorSprites, ArtDownload, SpriteLibrary
from .tooltip import draw_panel, draw_tooltip
from .ui import GameUI
from .vision import Vision

FPS = 60
DT = 1 / FPS
PX_PER_TILE = 33  # body radius on screen = collision radius (tiles, from the core) × this
HOLD_REPEAT_SECONDS = 0.12  # holding the mouse button repeats the click this often (move/attack)
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

    def __init__(self, seed: int | None = None, size: tuple[int, int] = (1280, 720), new_game: bool = False, save_dir: Path | None = None,
                 download_art: bool = True, art_dir: Path | None = None):
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
        self.hovered_drop: int | None = None
        self.ground_view = GroundView(self.fonts["tiny"])
        self.inventory_view = InventoryWindow(self)
        self.stash_view = StashWindow(self)
        self.shop_view = ShopWindow(self)
        self.waypoint_menu: list[tuple[pygame.Rect, str]] | None = None  # open while standing on a waypoint
        self.stash_store = SaveStore.for_stash(save_dir)
        self.talk_to: str | None = None  # NPC role being walked to (vendor | stash)
        self.hold: dict | None = None  # left button held in the world: {"target": monster id or None, "timer": s}
        self.death_text: str | None = None  # set while the character lies dead, waiting for a click
        self.vision = Vision()
        self.sprites = ActorSprites(SpriteLibrary(art_dir=art_dir))
        self.art = ArtDownload(self.sprites.lib)
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
        self._check_art(download_art)

    # ------------------------------------------------------------ art check on start

    def _check_art(self, download: bool) -> None:
        """Every time the game opens: are all sprite sheets in the art folder? Missing ones are downloaded
        behind a loading screen (Esc plays at once and lets the download go on in the background)."""
        lib = self.sprites.lib
        missing = lib.missing()
        no_look = lib.without_look(t.id for t in self.data.monsters.types)
        print(f"[art] {lib.art_dir / 'sprites'}: {lib.total() - len(missing)}/{lib.total()} sheets ready", flush=True)
        if no_look:
            print(f"[art] no art defined for: {', '.join(no_look)} (magenta stand-ins)", flush=True)
            self.hud.add(f"No art yet for: {', '.join(no_look)} (magenta squares).", C.MUTED)
        if not missing:
            return
        if not download:
            self.hud.add(f"{len(missing)} sprite sheet(s) missing (magenta squares). Run: python -m aethelgard.tools.fetch_art", C.MUTED)
            return
        print(f"[art] downloading {len(missing)} missing sheet(s)…", flush=True)
        self.art.start()
        self._loading_screen()

    def _loading_screen(self) -> None:
        while self.art.state == "running":
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                    return
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    self.hud.add("The art keeps downloading in the background; sprites appear when it ends.", C.MUTED)
                    return
            self._draw_loading(["Checking the art and downloading what is missing…", self.art.status_text.split("…")[0]],
                               self.art.fraction, "Esc: play now with placeholders (the download goes on)")
            self.clock.tick(30)
        note = self.art.poll()
        for line in self.art.problems if self.art.state == "failed" else []:
            print(f"[art]   - {line}", flush=True)
        print(f"[art] {note}", flush=True)
        if self.art.state != "failed":
            self.hud.add(note, C.GOLD)
            return
        lines = ["Some art could not be downloaded:", *self.art.problems[:6]]
        while True:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.running = False
                    return
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_r:
                    self.art.start()
                    return self._loading_screen()
                if (ev.type == pygame.KEYDOWN and ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE)) or ev.type == pygame.MOUSEBUTTONDOWN:
                    self.hud.add("Missing sprites show as magenta squares. Retry: python -m aethelgard.tools.fetch_art", C.LIFE)
                    return
            self._draw_loading(lines, 1.0, "R: try again  ·  Enter: play with placeholders", error=True)
            self.clock.tick(30)

    def _draw_loading(self, lines: list[str], fraction: float, hint: str, error: bool = False) -> None:
        screen = self.screen
        W, H = screen.get_size()
        screen.fill(C.BG)
        title = self.fonts["title"].render("Aethelgard", True, C.GOLD)
        screen.blit(title, title.get_rect(midbottom=(W // 2, H // 2 - 70)))
        y = H // 2 - 50
        for i, text in enumerate(lines):
            for part in _wrap(text, self.fonts["small"], W - 160):
                img = self.fonts["body" if i == 0 else "small"].render(part, True, C.LIFE if error and i else C.TEXT)
                screen.blit(img, img.get_rect(midtop=(W // 2, y)))
                y += 26 if i == 0 else 20
        bar = pygame.Rect(W // 2 - 220, max(y + 12, H // 2 + 20), 440, 14)
        pygame.draw.rect(screen, (0, 0, 0), bar)
        pygame.draw.rect(screen, C.LIFE if error else C.XP, (bar.x, bar.y, bar.w * max(0.0, min(1.0, fraction)), bar.h))
        pygame.draw.rect(screen, C.PANEL_EDGE, bar, 1)
        img = self.fonts["small"].render(hint, True, C.MUTED)
        screen.blit(img, img.get_rect(midtop=(W // 2, bar.bottom + 14)))
        pygame.display.flip()

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
        bus.subscribe(MonsterKilled, self._on_monster_killed)
        bus.subscribe(WaypointReached, self._on_waypoint)
        bus.subscribe(StashChanged, lambda e: self.stash_store.save_stash(self.world.stash))
        self.world.stash = self.stash_store.load_stash(self.data)  # the same file for every character

    @property
    def windows(self):
        """Item windows, front first: the service one (stash or shop) and the inventory."""
        return (self.stash_view, self.shop_view, self.inventory_view)

    def window_at(self, pos):
        return next((w for w in self.windows if w.contains(pos)), None)

    def close_windows(self) -> None:
        for w in self.windows:
            w.open = False
        self.waypoint_menu = None

    def _on_waypoint(self, e: WaypointReached) -> None:
        if e.player_id != self.session.player_id:
            return
        if e.new:
            self.hud.add(f"Waypoint activated: {self.area_names.get(e.area_id, e.area_id)}", C.WAYPOINT)
        self.open_waypoints()

    def open_waypoints(self) -> None:
        """List of every waypoint area in act order; the ones not found yet are greyed out."""
        W, H = self.screen.get_size()
        areas = [a for a in self.data.all_areas if a.waypoint]
        top = H // 2 - 20 * len(areas) - 30
        self.waypoint_menu = [(pygame.Rect(W // 2 - 150, top + 44 + i * 40, 300, 34), a.id) for i, a in enumerate(areas)]

    def _on_monster_killed(self, e: MonsterKilled) -> None:
        """The world removes the monster right after this event, so its body stays here, on the client."""
        m = self.world.monsters.get(e.monster.instance_id)
        if m is not None:  # no look: the magenta stand-in lies down too
            self.sprites.add_corpse(m, self.monster_layers(m) or {}, self.monster_scale(m))

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
        if e.player_id != self.session.player_id:
            return
        d = e.drop
        text = drop_name(d)
        if isinstance(d, ItemDrop):
            text += f"  ({d.item.base.name}, iLvl {d.item.item_level})"
        if not e.stored:
            self.hud.add(f"Backpack full: {text} stays on the ground.", C.MUTED)
        elif isinstance(d, GoldDrop):
            self.hud.add(f"+{text}", C.GOLD)
        else:
            self.hud.add(text, C.RARITY[int(d.item.rarity)] if isinstance(d, ItemDrop) else C.MUTED)

    def _on_exit(self, e: ExitReached) -> None:
        kind = ExitKind(e.kind)
        w = self.world
        if kind in (ExitKind.PORTAL_TO_TOWN, ExitKind.PORTAL_BACK):
            w.take_portal(e.player_id)
            self._area_changed()
        elif kind == ExitKind.STAIRS_DOWN:
            self.load_area(w.area_id, w.floor + 1, arrive_at=ExitKind.STAIRS_UP)
        elif kind == ExitKind.STAIRS_UP:
            self.load_area(w.area_id, w.floor - 1, arrive_at=ExitKind.STAIRS_DOWN)
        elif e.target_area:
            self.load_area(e.target_area, 0, came_from=w.area_id)

    def _on_death(self, e: PlayerDied) -> None:
        """The character stays on the floor, the screen turns gray, and a click takes them back to town."""
        if e.player_id != self.session.player_id:
            return
        r = self.session.apply_death()
        extra = "".join(f", {v:.0f} {self.session.proficiencies[k].name} XP" for k, v in r.xp_lost.items())
        self.death_text = f"Lost {r.gold_lost} gold{extra}."
        self.hud.add(f"You died. {self.death_text}", C.LIFE)
        self.hold = None
        self.ui.close_all()
        self.close_windows()

    @property
    def dead(self) -> bool:
        return self.player.state == "dead"

    def respawn(self) -> None:
        self.world.apply(Respawn(self.session.player_id))
        self.death_text = None
        self.load_area(self.data.town_id)

    def load_area(self, area_id: str, floor: int = 0, came_from: str | None = None, arrive_at: ExitKind | None = None) -> None:
        self.world.enter_area(area_id, floor, came_from, arrive_at)
        self._area_changed()

    def _area_changed(self) -> None:
        """Map, fog of war and title for whatever area the world is in now."""
        area_id, floor = self.world.area_id, self.world.floor
        area = self.data.area(area_id)
        self.map = MapRenderer(self.world.layout, area.kind)
        self.vision.enter((area_id, floor), self.world.layout, reveal_all=area.kind == "town")
        self.vision.update(self.player.x, self.player.y)
        suffix = f" – floor {floor + 1}" if area.kind == "dungeon" else ""
        level = "" if area.kind == "town" else f"  (level {area.level_for_floor(floor)})"
        self.hud.area_title = f"{area.name}{suffix}{level}"
        self.floaters.clear()
        self.sprites.clear()
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
            sx, sy = self.cam.to_screen(m.x, m.y)
            box = self.sprites.rects.get(("m", m.id))
            if box is not None and box.w > 0:  # drawn as a sprite: its box (a little margin), closest feet first
                inside = box.inflate(8, 8).collidepoint(mx, my)
                d = math.hypot(sx - mx, sy - my)
            else:
                r = self.body_px(m)
                d = math.hypot(sx - mx, sy - r - my)
                inside = d <= r + 12
            if inside and d < best_d:
                best, best_d = m, d
        return best

    @staticmethod
    def body_px(actor) -> int:
        return max(6, round(actor.radius * PX_PER_TILE))

    def slot_under_mouse(self) -> int | None:
        return self.hud.slot_at(pygame.mouse.get_pos(), len(self.session.skill_bar), self.screen.get_size())

    def visible_drops(self):
        e = self.vision.explored
        return [g for g in self.world.ground.values() if e is not None and e[round(g.y), round(g.x)]]

    def _update_hover(self) -> None:
        """What a click would act on. With Alt held, a drop label wins over a monster (labels sit on top)."""
        mouse = pygame.mouse.get_pos()
        over_hud = self.dead or self.ui.over_ui or self.window_at(mouse) is not None or self.over_waypoint_menu(mouse)
        over_hud = over_hud or self.slot_under_mouse() is not None
        if over_hud:
            self.hovered = self.hovered_drop = None
            return
        drop = self.ground_view.at(pygame.mouse.get_pos(), self.cam, self.visible_drops())
        monster = self.monster_under_mouse()
        if drop is not None and (monster is None or self.keys.held("showItems")):
            self.hovered, self.hovered_drop = None, drop
        else:
            self.hovered, self.hovered_drop = monster, None if monster else drop

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
        target = self.hovered or self.nearest_monster(max(3.0, self.world.player_reach(self.player)))  # a bow reaches farther
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
            if not self.dead and any(w.handle(ev) for w in self.windows):
                continue
            if ev.type == pygame.MOUSEBUTTONDOWN:
                self._on_click(ev.button)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                self.hold = None
            elif ev.type == pygame.KEYDOWN:
                self._on_key(ev.key)
        if self.dead:
            return
        self._repeat_held_click()

        # guard: hold the guard key, shield pointed at the mouse
        held = not self.ui.typing and self.keys.held("guard")
        gx, gy = self.mouse_grid()
        self.world.apply(SetGuard(pid, bool(held), gx - self.player.x, gy - self.player.y))

    def over_waypoint_menu(self, pos) -> bool:
        if not self.waypoint_menu:
            return False
        boxes = [b for b, _ in self.waypoint_menu]
        return boxes[0].unionall(boxes).inflate(40, 80).collidepoint(pos)

    def npc_under_mouse(self) -> str | None:
        """Role of the NPC under the cursor (its body on screen)."""
        mx, my = pygame.mouse.get_pos()
        for _, (cell, props) in self.world.layout.markers.items():
            sx, sy = self.cam.to_screen(*cell)
            if pygame.Rect(sx - 14, sy - 50, 28, 54).collidepoint(mx, my):
                return props.get("role")
        return None

    def _open_service(self, role: str) -> None:
        """Talking to the merchant or the stash opens its window with the backpack beside it."""
        self.close_windows()
        (self.shop_view if role == "vendor" else self.stash_view).open = True

    def _on_click(self, button: int) -> None:
        if self.dead:
            if button == 1:
                self.respawn()
            return
        if self.waypoint_menu is not None:
            if button == 1:
                for r, area_id in self.waypoint_menu:
                    if r.collidepoint(pygame.mouse.get_pos()) and area_id in self.session.waypoints:
                        msg = self.world.apply(TravelWaypoint(self.session.player_id, area_id))
                        if msg:
                            self.hud.add(msg, C.MUTED)
                        else:
                            self._area_changed()
                        break
            self.waypoint_menu = None
            return
        slot = self.slot_under_mouse()
        if slot is not None:  # the bar: left picks a skill for the key, right clears it
            if button == 1:
                self.ui.open_picker(slot)
            elif button == 3:
                self.bind_slot(slot, None)
            return
        if button != 1:
            return
        role = self.npc_under_mouse()
        if role in ("vendor", "stash"):  # walk there; the window opens on arrival (tick)
            self.talk_to = role
            self.world.apply(MoveTo(self.session.player_id, *self.mouse_grid()))
            self.hold = None
            return
        self.talk_to = None
        if self.hovered_drop is not None:  # walk to it and pick it up; holding does not repeat this
            self.world.apply(PickUp(self.session.player_id, self.hovered_drop))
            self.hold = None
            return
        target = self.hovered if not self.player.guarding else None
        in_place = self.keys.held("standStill")
        self.hold = {"target": target.id if target else None, "timer": HOLD_REPEAT_SECONDS, "in_place": in_place}
        self._world_click(self.hold["target"], in_place)

    def _world_click(self, target_id: int | None, in_place: bool = False) -> None:
        pid = self.session.player_id
        if in_place:  # Shift: attack toward the mouse without moving (D2 "stand still")
            self.world.apply(AttackInPlace(pid, *self.mouse_grid()))
        elif target_id is not None:
            self.world.apply(AttackTarget(pid, target_id))
        else:
            self.world.apply(MoveTo(pid, *self.mouse_grid()))

    def _repeat_held_click(self) -> None:
        """Holding the left button acts like clicking again and again (D2): it keeps walking toward the
        cursor, or keeps attacking the monster it was pressed on until that one dies."""
        if self.hold is None:
            return
        if not pygame.mouse.get_pressed()[0]:
            self.hold = None
            return
        if self.ui.over_ui or self.window_at(pygame.mouse.get_pos()):  # over a window: pause, release ends it
            return
        self.hold["timer"] -= DT
        if self.hold["timer"] > 0:
            return
        self.hold["timer"] = HOLD_REPEAT_SECONDS
        target = self.hold["target"]
        in_place = self.keys.held("standStill")  # holding Shift keeps striking where you stand
        if in_place:
            self._world_click(None, True)
            return
        if target is not None and target not in self.world.monsters:
            self.hold = None  # the target died: stand still until the next click
            return
        self._world_click(target)

    def _on_key(self, key: int) -> None:
        if self.dead:
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.respawn()
            elif self.keys.action_for(key) == "cancel":
                self.running = False
            return
        slot = self.keys.slot_for(key)
        if slot is not None:
            node_id = self.ui.hovered_node(pygame.mouse.get_pos())
            if node_id is not None:  # D2 style: hover a skill in the trees and press the key to bind it
                self.bind_slot(slot, node_id)
            else:
                self.use_slot(slot)
            return
        action = self.keys.action_for(key)
        if action == "townPortal":
            msg = self.world.apply(OpenPortal(self.session.player_id))
            if msg:
                self.hud.add(msg, C.MUTED)
        elif action in CONSUMABLE_KEYS:
            msg = self.world.apply(UseConsumable(self.session.player_id, CONSUMABLE_KEYS[action]))
            if msg:
                self.hud.add(msg, C.MUTED)
        elif action == "save":
            self.save_game()
        elif action == "skillTrees":
            self.ui.toggle_tree()
        elif action == "inventory":
            self.inventory_view.toggle()
        elif action == "map":
            self.big_map = not self.big_map
        elif action == "cancel":
            if self.ui.any_open or any(w.open for w in self.windows) or self.waypoint_menu is not None:
                self.ui.close_all()
                self.close_windows()
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
        self.map.draw_markers(screen, cam, self.fonts["tiny"], self.area_names, self.vision.explored)
        drops = self.visible_drops()
        self.sprites.draw_corpses(screen, cam, self.vision.sees)  # bodies lie under what they dropped
        self.ground_view.draw_shapes(screen, cam, drops)
        self._draw_portal()

        # monsters outside the field of view are not drawn
        actors = [(m.x + m.y, "m", m) for m in self.world.monsters.values() if self.vision.sees(m.x, m.y)]
        actors.append((self.player.x + self.player.y, "p", self.player))
        for _, kind, a in sorted(actors, key=lambda t: t[0]):
            if kind == "m":
                self._draw_monster(a)
            else:
                self._draw_player(a)

        for proj in self.world.projectiles.values():
            self._draw_projectile(proj)
        self.ground_view.draw_labels(screen, cam, drops, self.keys.held("showItems"), self.hovered_drop, pygame.mouse.get_pos())

        for f in self.floaters:
            sx, sy = cam.to_screen(f.gx, f.gy)
            img = self.fonts["small"].render(f.text, True, f.color)
            img.set_alpha(int(255 * max(0, min(1, f.life / 0.9))))
            screen.blit(img, (sx - img.get_width() // 2, sy - 56 - (0.9 - f.life) * 45))

        self.vision.draw_automap(screen, self.world.layout, self.player.x, self.player.y, big=self.big_map)
        if self.art.status_text:
            img = self.fonts["small"].render(self.art.status_text, True, C.MUTED)
            screen.blit(img, img.get_rect(midtop=(screen.get_width() // 2, 8)))
        self.hud.draw(screen, self.world, self.player, self.hovered)
        self.ui.draw(screen)
        if self.dead:
            self._draw_death_screen()
        else:
            if self.stash_view.open or self.shop_view.open:  # a service window shows its own backpack
                self.inventory_view.open = False
            for w in reversed(self.windows):
                w.draw(screen)
            self._draw_waypoint_menu()
            tip = None if self.window_at(pygame.mouse.get_pos()) else self.skill_tooltip()
            if tip:
                draw_tooltip(screen, self.fonts, tip, pygame.mouse.get_pos())
        pygame.display.flip()

    def _draw_death_screen(self) -> None:
        """Everything in gray, slightly darker, with a panel that says what was lost and how to go on."""
        screen = self.screen
        gray = pygame.transform.grayscale(screen)
        shade = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 90))
        gray.blit(shade, (0, 0))
        screen.blit(gray, (0, 0))
        W, H = screen.get_size()
        title = self.fonts["title"].render("You died", True, C.LIFE)
        lines = [self.death_text or "", "Click (or press Enter) to return to town."]
        imgs = [self.fonts["body"].render(t, True, C.TEXT if i == 0 else C.HINT) for i, t in enumerate(lines)]
        w = max(title.get_width(), *(i.get_width() for i in imgs)) + 60
        rect = pygame.Rect(W // 2 - w // 2, H // 2 + 50, w, 120)  # below the body, which lies at the center
        draw_panel(screen, rect, edge=C.LIFE)
        screen.blit(title, title.get_rect(midtop=(rect.centerx, rect.y + 16)))
        for k, img in enumerate(imgs):
            screen.blit(img, img.get_rect(midtop=(rect.centerx, rect.y + 56 + 26 * k)))

    def _draw_waypoint_menu(self) -> None:
        if not self.waypoint_menu:
            return
        screen, f = self.screen, self.fonts
        boxes = [b for b, _ in self.waypoint_menu]
        panel = boxes[0].unionall(boxes).inflate(40, 80)
        draw_panel(screen, panel, edge=C.WAYPOINT)
        screen.blit(f["body"].render("Waypoints", True, C.WAYPOINT), (panel.x + 20, panel.y + 10))
        mouse = pygame.mouse.get_pos()
        for r, area_id in self.waypoint_menu:
            known = area_id in self.session.waypoints
            hot = known and r.collidepoint(mouse)
            pygame.draw.rect(screen, (40, 50, 80) if hot else C.PANEL, r, border_radius=4)
            pygame.draw.rect(screen, C.WAYPOINT if known else (50, 50, 56), r, 1, border_radius=4)
            name = self.area_names.get(area_id, area_id) + ("  (here)" if area_id == self.world.area_id else "")
            img = f["small"].render(name if known else f"{name}  (not found yet)", True, C.TEXT if known else C.PANEL_EDGE)
            screen.blit(img, img.get_rect(center=r.center))
        hint = f["tiny"].render("click to travel · Esc or click outside closes", True, C.MUTED)
        screen.blit(hint, hint.get_rect(midbottom=(panel.centerx, panel.bottom - 6)))

    def _draw_portal(self) -> None:
        """The player's town portal, where it stands in this area (in the world, or beside the town waypoint)."""
        spot = self.world.portal_spot(self.session.player_id)
        e = self.vision.explored
        if spot is None or (e is not None and not e[round(spot[1]), round(spot[0])]):
            return
        sx, sy = self.cam.to_screen(*spot)
        screen = self.screen
        pygame.draw.ellipse(screen, (20, 40, 90), (sx - 14, sy - 50, 28, 52))
        pygame.draw.ellipse(screen, (90, 150, 255), (sx - 14, sy - 50, 28, 52), 3)
        pygame.draw.ellipse(screen, (170, 210, 255), (sx - 6, sy - 38, 12, 30))
        portal = self.world.portals[self.session.player_id]
        label = "Town Portal" if self.world.area_id != self.data.town_id else f"Portal to {self.area_names.get(portal.area_id, portal.area_id)}"
        img = self.fonts["tiny"].render(label, True, (150, 190, 255))
        screen.blit(img, (sx - img.get_width() // 2, sy - 66))

    def _draw_projectile(self, proj) -> None:
        """Flies at chest height; the line shows where it is going (it never turns)."""
        if not self.vision.sees(proj.x, proj.y):
            return
        lift = 24
        sx, sy = self.cam.to_screen(proj.x, proj.y)
        tx, ty = self.cam.to_screen(proj.x - proj.dir_x * 0.8, proj.y - proj.dir_y * 0.8)
        head, tail = (sx, sy - lift), (tx, ty - lift)
        screen = self.screen
        if proj.look == "arrow":
            pygame.draw.line(screen, (0, 0, 0), tail, head, 5)
            pygame.draw.line(screen, (226, 206, 160), tail, head, 3)
            pygame.draw.circle(screen, (250, 250, 250), head, 3)
        elif proj.look == "shield":
            pygame.draw.circle(screen, C.SHIELD, head, 8)
            pygame.draw.circle(screen, (0, 0, 0), head, 8, 1)
        else:  # magic bolt: glow with a short trail
            pygame.draw.line(screen, (120, 90, 200), tail, head, 4)
            pygame.draw.circle(screen, (190, 150, 255), head, 6)
            pygame.draw.circle(screen, (250, 240, 255), head, 3)

    # ---- looks (data/ui/sprites.json); None = placeholder shapes

    def player_layers(self, p) -> dict[str, str] | None:
        return self.sprites.lib.layers("player", [(slot, item.base.id) for slot, item in p.session.gear.slots.items()])

    def monster_layers(self, m: MonsterActor) -> dict[str, str] | None:
        return self.sprites.lib.layers(m.monster.type.id)

    def monster_scale(self, m: MonsterActor) -> float:
        return m.monster.rank.size_mult * self.sprites.lib.draw_scale(m.monster.type.id)

    def _draw_player(self, p) -> None:
        layers = self.player_layers(p)
        if layers is None:
            self._draw_player_shapes(p)
            return
        sx, sy = self.cam.to_screen(p.x, p.y)
        off_swing = p.state == "attacking" and p.attack_hand == "off" and p.session.gear.dual_wielding
        if off_swing:  # the left arm strikes: the figure is drawn mirrored, weapons swapped
            layers = self.sprites.lib.swap_hands(layers)
        self.sprites.draw_actor(self.screen, self.cam, ("p", p.player_id), p, layers, scale=self.sprites.lib.draw_scale("player"),
                                guarding=p.guarding, mirrored=off_swing, attack_anim="shoot" if p.session.gear.has_bow else "swing")
        if p.guarding and p.state != "dead":
            self._draw_guard_arc(p, sx, sy)

    def _draw_guard_arc(self, p, sx: int, sy: int) -> None:
        """Where the guard covers: the arc in front, or a full ring while Bulwark is up."""
        fx, fy = self.cam.to_screen(p.x + p.face_x, p.y + p.face_y)
        ang = math.atan2(fy - sy, fx - sx)
        arc = math.radians(p.session.data.balance.guard.arc_degrees) / 2
        if p.bulwark_left > 0:
            pygame.draw.circle(self.screen, C.GUARD, (sx, sy - 20), 36, 2)
        else:
            pygame.draw.arc(self.screen, C.GUARD, (sx - 36, sy - 56, 72, 72), -ang - arc, -ang + arc, 2)

    def _draw_player_shapes(self, p) -> None:
        sx, sy = self.cam.to_screen(p.x, p.y)
        screen = self.screen
        if p.state == "dead":  # lying on the floor, shield and sword dropped beside the body
            pygame.draw.ellipse(screen, (0, 0, 0), (sx - 26, sy - 6, 52, 12))
            pygame.draw.rect(screen, C.PLAYER_BODY, (sx - 22, sy - 12, 34, 14), border_radius=5)
            pygame.draw.circle(screen, C.PLAYER_HEAD, (sx + 18, sy - 6), 7)
            pygame.draw.rect(screen, C.SHIELD, (sx - 30, sy - 2, 14, 8))
            pygame.draw.line(screen, C.SWORD, (sx - 8, sy + 6), (sx + 22, sy + 10), 3)
            return
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
                self._draw_guard_arc(p, sx, sy)
            else:
                pygame.draw.rect(screen, C.SHIELD, (sx - 17, sy - 32, 8, 16))
        elif p.session.gear.dual_wielding:  # second blade in the left hand
            pygame.draw.line(screen, C.SWORD, (sx - 10, sy - 28), (sx - 14 + ux * 10, sy - 46 + uy * 6), 3)
        swing = 0.0
        if p.state == "attacking" and p.attack_duration > 0:
            swing = math.sin(math.pi * min(1, p.attack_elapsed / p.attack_duration))
        tip = (sx + 10 + ux * (14 + 14 * swing), sy - 30 + uy * (14 + 14 * swing) - 16 * (1 - swing))
        pygame.draw.line(screen, C.SWORD, (sx + 8, sy - 28), tip, 3)

    def _draw_monster(self, m: MonsterActor) -> None:
        sx, sy = self.cam.to_screen(m.x, m.y)
        rank = m.monster.rank.id
        r = self.body_px(m)
        name_color = C.rank_name_color(rank)
        screen = self.screen
        if m.aura is not None:  # leader: a faint ring shows how far its aura reaches
            ar = m.aura.radius_tiles
            pygame.draw.ellipse(screen, (110, 90, 40), (sx - ar * 32, sy - ar * 16, ar * 64, ar * 32), 1)
        layers = self.monster_layers(m)
        if m is self.hovered:  # outline at the feet, in the color of its name
            pygame.draw.ellipse(screen, name_color, (sx - r - 4, sy - r // 3 - 2, 2 * r + 8, 2 * r // 3 + 4), 2)
        if layers is None:  # no art defined for this type: magenta stand-in, with a ring that closes until the impact
            box = self.sprites.draw_missing(screen, self.cam, ("m", m.id), m, self.monster_scale(m))
            if m.state == "attacking":
                k = min(1.0, m.attack_elapsed / (m.attack_duration * 0.5))
                pygame.draw.circle(screen, (255, 255, 255), box.center, int(box.h / 2 + 10 * (1 - k)), 1)
        else:  # the sprite's own swing shows the attack coming
            box = self.sprites.draw_actor(screen, self.cam, ("m", m.id), m, layers,
                                          attack_anim=self.sprites.lib.attack_anim(m.monster.type.id), scale=self.monster_scale(m))
        top = min(box.top, sy - 2 * r) + 6
        frac = m.monster.hp / m.monster.max_hp
        pygame.draw.rect(screen, (0, 0, 0), (sx - 16, top - 10, 32, 4))
        pygame.draw.rect(screen, C.LIFE, (sx - 16, top - 10, 32 * frac, 4))
        first_of_pack = not m.pack_ids or m.pack_ids[0] == m.id or m.pack_ids[0] not in self.world.monsters
        if m is self.hovered or rank in ("unique", "boss") or (rank in ("champion", "berserker") and first_of_pack):
            img = self.fonts["tiny"].render(m.monster.name, True, name_color)  # details go to the target frame
            screen.blit(img, (sx - img.get_width() // 2, top - 26))

    # ------------------------------------------------------------ loop

    def tick(self) -> None:
        self.handle_events()
        self.world.step(DT)  # the world keeps running while windows are open
        self._update_hover()
        if self.talk_to and self.world.npc_near(self.player, self.talk_to):
            self.world.apply(MoveTo(self.session.player_id, self.player.x, self.player.y))  # stop beside the NPC
            self._open_service(self.talk_to)
            self.talk_to = None
        for w, role in ((self.stash_view, "stash"), (self.shop_view, "vendor")):  # walking away closes it
            if w.open and not self.world.npc_near(self.player, role):
                w.open = False
        if self.waypoint_menu is not None and not self.world.at_waypoint(self.player):
            self.waypoint_menu = None
        self.vision.update(self.player.x, self.player.y)
        self.sprites.tick(DT)
        note = self.art.poll()
        if note:
            self.hud.add(note, C.GOLD if self.art.state == "done" else C.LIFE)
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


def _wrap(text: str, font: pygame.font.Font, width: int) -> list[str]:
    words, lines, line = text.split(" "), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if font.size(test)[0] > width and line:
            lines.append(line)
            line = w
        else:
            line = test
    return [*lines, line] if line else lines
