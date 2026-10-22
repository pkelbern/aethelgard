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
    ActOpened,
    ExitReached,
    FloatingText,
    ItemCrafted,
    LootPicked,
    MonsterKilled,
    PlayerDied,
    ProficiencyLeveledUp,
    SkillLeveledUp,
    StashChanged,
    WaypointReached,
)
from ..core.items import ChestDrop, GoldDrop, ItemDrop, drop_name
from ..core.progression import Bind
from ..core.saves import SaveStore
from ..core.session import GameSession
from ..core.simulation import run_act
from ..core.world import (
    GROUND_BOW_SKILLS,
    AttackInPlace,
    AttackTarget,
    MonsterActor,
    MoveTo,
    OpenPortal,
    PickUp,
    ReleaseSkill,
    Respawn,
    SetGuard,
    TravelWaypoint,
    UseConsumable,
    UseSkill,
    World,
    is_implemented,
)
from ..core.world_gen import ExitKind
from ..core.xp_log import XpLog
from ..tools.fetch_sounds import load_icon_config, load_sound_config
from . import palette as C
from .audio import Audio
from .character_view import CharacterWindow
from .game_menu import GameMenu
from .gamepad import Gamepad
from .hud import Hud
from .icons import Icons
from .iso import Camera, MapRenderer
from .item_windows import CraftWindow, InventoryWindow, ShopWindow, StashWindow
from .keys import CONSUMABLE_KEYS, KeyMap
from .loot_view import GroundView
from .screens import CharacterSelectScreen, CreditsScreen, DialogueScreen, OptionsScreen, TitleScreen
from .settings import Settings
from .skill_drag import RIGHT, SkillDrag
from .skill_tree_view import SkillTreeWindow
from .spell_view import SpellView
from .sprites import ActorSprites, ArtDownload, EffectArt, SpriteLibrary
from .strings import Strings
from .tilemap import MapArt
from .tooltip import draw_panel, draw_tooltip
from .ui import GameUI
from .vision import Vision

FPS = 60
DT = 1 / FPS
SHAKE_SECONDS, SHAKE_PIXELS = 0.18, 5  # a critical hit jolts the screen (M1-05)
PX_PER_TILE = 33  # body radius on screen = collision radius (tiles, from the core) × this
HOLD_REPEAT_SECONDS = 0.12  # holding the mouse button repeats the click this often (move/attack)
CONSOLE_HELP = (
    "save · load · bag · xp <prof> <amount> · level <prof> <n> · point <node> · skill <node> [level] · area <id> [floor] · "
    "god · kill · heal · reveal · sim · seed · stress [n] (n monsters around you, for the FPS counter: F3)"
)


@dataclass
class Floater:
    gx: float
    gy: float
    text: str
    color: tuple
    life: float = 0.9


class Game:
    """``new_game`` ignores an existing save; ``save_dir`` overrides the per-user save folder; ``title`` opens on the
    title screen (pick or make a character) instead of going straight into the last save; ``size`` overrides the
    window size of the options."""

    def __init__(self, seed: int | None = None, size: tuple[int, int] | None = None, new_game: bool = False, save_dir: Path | None = None,
                 download_art: bool = True, art_dir: Path | None = None, title: bool = False):
        pygame.init()
        pygame.display.set_caption("Aethelgard")
        self.store = SaveStore(save_dir)
        self.settings = Settings.load(self.store.directory)
        self.tr = Strings(self.settings.language)
        if size is None:
            size = tuple(self.settings.resolution)
        self.screen = pygame.display.set_mode(size, pygame.FULLSCREEN if self.settings.fullscreen and size == tuple(self.settings.resolution) else 0)
        size = self.screen.get_size()
        self.clock = pygame.time.Clock()
        self.fonts = {
            "title": pygame.font.Font(None, 34),
            "body": pygame.font.Font(None, 24),
            "small": pygame.font.Font(None, 20),
            "tiny": pygame.font.Font(None, 17),
        }
        self.data = GameData.load()
        self.seed = seed if seed is not None else time.time_ns() & 0xFFFFFFFF
        self.keys = KeyMap.load(self.data.skills.bar_slots, self.settings.keys)
        self.cam = Camera(*size)
        self.hud = Hud(self.fonts, self.keys)
        self.hovered: MonsterActor | None = None
        self.hovered_drop: int | None = None
        self.ground_view = GroundView(self.fonts["tiny"])
        self.inventory_view = InventoryWindow(self)
        self.stash_view = StashWindow(self)
        self.shop_view = ShopWindow(self)
        self.character_view = CharacterWindow(self)
        self.tree_view = SkillTreeWindow(self)
        self.drag = SkillDrag(self)
        self.pad = Gamepad(self)
        self.waypoint_menu: list[tuple[pygame.Rect, str]] | None = None  # open while standing on a waypoint
        self.waypoint_acts: list[tuple[str, tuple[int, int]]] = []  # act names over the waypoint columns
        self.stash_store = SaveStore.for_stash(save_dir)
        self.talk_to: str | None = None  # map name of the NPC being walked to (merchant, stash, crafter, townsfolk)
        self.hold: dict | None = None  # left button held in the world: {"target": monster id or None, "timer": s}
        self.death_text: str | None = None  # set while the character lies dead, waiting for a click
        self.vision = Vision()
        self.sprites = ActorSprites(SpriteLibrary(art_dir=art_dir))
        sound_cfg = load_sound_config()
        self.art = ArtDownload(self.sprites.lib, extras=[sound_cfg, load_icon_config()])
        self.icons = Icons(self.sprites.lib.art_dir, self.art.extras[1])
        self.hud.icons = self.icons
        self.audio = Audio(self, sound_cfg, self.sprites.lib.art_dir, self.settings)
        self.shake = 0.0  # seconds of screen shake left (a critical hit, M1-05)
        self.effects = EffectArt(self.sprites.lib)
        self.effects.icons = self.icons
        self.spell_view = SpellView(self)
        self.menu = GameMenu(self)
        self.options = OptionsScreen(self)
        self.credits = CreditsScreen(self)
        self.title = TitleScreen(self)
        self.select = CharacterSelectScreen(self)
        self.dialogue = DialogueScreen(self)
        self.craft_view = CraftWindow(self)  # the crafter's bench (Act 2)
        self.show_fps = False
        self.ground_view.art = self.effects
        self.big_map = False
        self.floaters: list[Floater] = []
        self.map: MapRenderer | None = None
        self.area_names = {a.id: a.name for a in self.data.all_areas}
        self.running = True

        if title:  # the most recently played character is the default slot behind the title screen
            chars = SaveStore.characters(self.store.directory)
            self.store.slot = chars[0][0] if chars else self.store.slot
        loaded = None if new_game else self._read_save()
        self._bind(loaded or GameSession(self.data, self.seed))
        self.ui = GameUI(self, size)
        self.load_area(self.session.home_town)
        if loaded:
            self.hud.add(self.tr("save.welcomeBack", name=loaded.name), C.GOLD)
        self.hud.add(self.tr("game.worldSeed", seed=self.seed), C.MUTED)
        self._check_art(download_art)
        if title:
            self.title.open = True
            self.audio.music("title")

    # ------------------------------------------------------------ title screen and options

    def start_character(self, slot: str, new_name: str | None = None) -> None:
        """From the title screen: plays the save in ``slot``, or makes a new character called ``new_name`` there
        (an existing save with that file name is kept as the .bak copy when the new one is first saved)."""
        if self.store.slot != slot or new_name is not None:
            self.store.slot = slot
            loaded = None if new_name is not None else self._read_save()
            session = loaded or GameSession(self.data, self.seed, name=new_name or slot)
            self._bind(session)
            self.load_area(session.home_town)
            self.hud.add(self.tr("game.welcome" if loaded is None else "game.welcomeBack", name=session.name), C.GOLD)
            if new_name is not None:
                self.store.save_session(session)
        self.audio.theme = None
        self._play_area_music()

    def apply_settings(self, display: bool = False) -> None:
        """After a change in the options: keys, volumes and (``display``) the window; saved at once."""
        st = self.settings
        st.save(self.store.directory)
        self.tr = Strings(st.language)
        self.keys = KeyMap.load(self.data.skills.bar_slots, st.keys)
        self.hud.keys = self.keys
        self.audio.apply_volume()
        if display:
            size = tuple(st.resolution)
            self.screen = pygame.display.set_mode(size, pygame.FULLSCREEN if st.fullscreen else 0)
            self.cam = Camera(*self.screen.get_size())
            self.cam.follow(self.player.x, self.player.y, DT, snap=True)
            self.ui.resize(self.screen.get_size())

    def _play_area_music(self) -> None:
        area = self.data.area(self.world.area_id)
        boss_floor = area.boss is not None and self.world.floor == max(0, area.floors - 1)
        self.audio.music("boss" if boss_floor else area.kind)

    # ------------------------------------------------------------ art check on start

    def _check_art(self, download: bool) -> None:
        """Every time the game opens: are all sprite sheets in the art folder? Missing ones are downloaded
        behind a loading screen (Esc plays at once and lets the download go on in the background)."""
        lib = self.sprites.lib
        missing = self.art.missing()  # sheets and sounds
        sheets = len(lib.missing())  # the rest: sounds and icons
        no_look = lib.without_look(t.id for t in self.data.monsters.types)
        print(f"[art] {lib.art_dir / 'sprites'}: {lib.total() - sheets}/{lib.total()} sheets ready, {len(missing) - sheets} other file(s) missing", flush=True)
        if no_look:
            print(f"[art] no art defined for: {', '.join(no_look)} (magenta stand-ins)", flush=True)
            self.hud.add(self.tr("art.noLook", names=', '.join(no_look)), C.MUTED)
        if not missing:
            return
        if not download:
            self.hud.add(self.tr("art.missingFiles", count=len(missing)), C.MUTED)
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
                    self.hud.add(self.tr("art.inBackground"), C.MUTED)
                    return
            self._draw_loading(["Checking the art and downloading what is missing…", self.art.status_text.split("…")[0]],
                               self.art.fraction, "Esc: play now with placeholders (the download goes on)")
            self.clock.tick(30)
        note = self.art.poll()
        self._build_map()  # the tiles may have just arrived
        self.audio.reload()
        self.icons.reload()
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
                    self.hud.add(self.tr("art.retryLater"), C.LIFE)
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
        bus.subscribe(ItemCrafted, lambda e: e.player_id == session.player_id and self.hud.add(
            self.tr("craft.salvaged" if e.recipe_id == "salvage" else "craft.made", what=e.what), C.GOLD))
        bus.subscribe(ActOpened, lambda e: e.player_id == session.player_id and self.hud.add(
            self.tr("act.opened", town=self.area_names.get(e.town_id, e.town_id)), C.GOLD))
        bus.subscribe(SkillLeveledUp, lambda e: self.hud.add(self.tr("skill.leveledUp", name=self.data.node(e.skill_id).name, level=e.level), C.GOLD))
        self.spell_view.listen(bus)
        self.audio.listen(bus)
        bus.subscribe(FloatingText, lambda e: e.kind == "crit" and self._shake(SHAKE_SECONDS))
        bus.subscribe(StashChanged, lambda e: self.stash_store.save_stash(self.world.stash))
        if getattr(self, "xp_log", None) is not None:
            self.xp_log.close()
        stamp = time.strftime("%Y%m%d-%H%M%S")  # one CSV per play session, for playtests (M2-05)
        self.xp_log = XpLog(bus, self.store.directory / "xp_logs" / f"xp_{stamp}.csv", session.proficiencies)
        self.world.stash = self.stash_store.load_stash(self.data)  # the same file for every character

    @property
    def windows(self):
        """Painted windows, front first: the service one (stash, shop or crafter), the inventory, the sheet and the trees."""
        return (self.stash_view, self.shop_view, self.craft_view, self.inventory_view, self.character_view, self.tree_view)

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
            self.hud.add(self.tr("waypoint.activated", area=self.area_names.get(e.area_id, e.area_id)), C.WAYPOINT)
        self.open_waypoints()

    def open_waypoints(self) -> None:
        """Every waypoint area, one column per act the character has reached; the ones not found yet are greyed out."""
        W, H = self.screen.get_size()
        acts = [act for act in self.data.areas.acts if any(a.kind == "town" and a.id in self.session.waypoints for a in act.areas)]
        cols = [[a for a in act.areas if a.waypoint] for act in acts]
        rows = max((len(c) for c in cols), default=0)
        top = H // 2 - 20 * rows - 20
        left = W // 2 - 160 * len(cols)
        self.waypoint_menu = [(pygame.Rect(left + 320 * i + 10, top + 64 + j * 40, 300, 34), a.id)
                              for i, col in enumerate(cols) for j, a in enumerate(col)]
        self.waypoint_acts = [(act.name, (left + 320 * i + 160, top + 40)) for i, act in enumerate(acts)]

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
        self.hud.add(self.tr("save.saved"), C.GOLD)
        return str(path)

    def load_game(self) -> str:
        """Reloads the save in a fresh world and goes back to town (GDD 9.4)."""
        loaded = self._read_save()
        if loaded is None:
            return "no save to load"
        self._bind(loaded)
        self.load_area(loaded.home_town)
        self.hud.add(self.tr("save.loaded", name=loaded.name), C.GOLD)
        return "loaded"

    # ------------------------------------------------------------ core events

    def _on_level_up(self, e: ProficiencyLeveledUp) -> None:
        name = self.session.proficiencies[e.proficiency_id].name
        self.hud.add(self.tr("proficiency.leveledUp", name=name, new_level=e.new_level), C.GOLD)

    def _on_loot(self, e: LootPicked) -> None:
        if e.player_id != self.session.player_id:
            return
        d = e.drop
        text = drop_name(d)
        if isinstance(d, ChestDrop):
            self.hud.add(self.tr("loot.chestOpened"), C.GOLD)
            return
        if isinstance(d, ItemDrop):
            text += f"  ({d.item.base.name}, iLvl {d.item.item_level})"
        if not e.stored:
            self.hud.add(self.tr("loot.backpackFull", text=text), C.MUTED)
        elif isinstance(d, GoldDrop):
            self.hud.add(self.tr("loot.gold", text=text), C.GOLD)
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
        self.death_text = f"Lost {r.gold_lost} gold{extra}." + (" Your gear lies on your corpse: walk over it to take it back."
                                                              if self.session.corpses else "")
        self.hud.add(self.tr("death.youDied", death_text=self.death_text), C.LIFE)
        self.hold = None
        self.ui.close_all()
        self.close_windows()

    @property
    def dead(self) -> bool:
        return self.player.state == "dead"

    def respawn(self) -> None:
        self.world.apply(Respawn(self.session.player_id))
        self.death_text = None
        self.load_area(self.data.town_of(self.world.area_id))

    def load_area(self, area_id: str, floor: int = 0, came_from: str | None = None, arrive_at: ExitKind | None = None) -> None:
        self.world.enter_area(area_id, floor, came_from, arrive_at)
        self._area_changed()

    def _build_map(self) -> None:
        """Map renderer for the current area: tiles when the art is there, placeholder shapes otherwise."""
        area = self.data.area(self.world.area_id)
        art = MapArt.build(self.sprites.lib, self.world.layout, area.id, area.kind, self.data.npcs.npcs)
        self.map = MapRenderer(self.world.layout, area.kind, art)

    def _area_changed(self) -> None:
        """Map, fog of war and title for whatever area the world is in now."""
        area_id, floor = self.world.area_id, self.world.floor
        area = self.data.area(area_id)
        self._build_map()
        self.vision.enter((area_id, floor), self.world.layout, reveal_all=area.kind == "town")
        self.vision.update(self.player.x, self.player.y)
        suffix = f" – floor {floor + 1}" if area.kind == "dungeon" else ""
        level = "" if area.kind == "town" else f"  (level {area.level_for_floor(floor)})"
        self.hud.area_title = f"{area.name}{suffix}{level}"
        self.floaters.clear()
        self.sprites.clear()
        self.cam.follow(self.player.x, self.player.y, DT, snap=True)
        if not self.title.open:
            self._play_area_music()

    def _shake(self, seconds: float) -> None:
        self.shake = max(self.shake, seconds)

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
            self.hud.add(self.tr("bar.keyCleared", label=label), C.MUTED)
        else:
            name = self.data.node(node_id).name
            r = bar.bind(slot, node_id)
            ok = r is Bind.OK
            self.hud.add(self.tr("bar.onKey", name=name, key=label) if ok else self.tr("skill.cannot", name=name, reason=r.value), C.TEXT if ok else C.MUTED)

    def _on_key_up(self, key: int) -> None:
        """Letting go of a channelled skill's key (Whirlwind) stops it."""
        slot = self.keys.slot_for(key)
        self._release(self.session.skill_bar.skill_at(slot) if slot is not None else None)

    def _release(self, skill: str | None) -> None:
        if skill is not None and self.data.node(skill).channeled:
            self.world.apply(ReleaseSkill(self.session.player_id, skill))

    def set_right_skill(self, node_id: str | None) -> None:
        r = self.session.skill_bar.set_right(node_id)
        if node_id is None:
            self.hud.add(self.tr("bar.rightCleared"), C.MUTED)
        else:
            name = self.data.node(node_id).name
            ok = r is Bind.OK
            self.hud.add(self.tr("bar.onRight", name=name) if ok else self.tr("skill.cannot", name=name, reason=r.value), C.TEXT if ok else C.MUTED)

    def use_slot(self, slot: int, aim: tuple[float, float] | None = None) -> None:
        self.use_skill(self.session.skill_bar.skill_at(slot), self.tr("bar.emptyKey", key=self.keys.slot_label(slot)), aim)

    def use_skill(self, skill: str | None, empty: str, aim: tuple[float, float] | None = None) -> None:
        """A bar key or the right click: the skill aimed at the mouse (or the monster under it, or the nearest).
        ``aim`` (the gamepad): aimed at that point, and at the nearest monster instead of the one under the mouse."""
        if skill is None:
            self.hud.add(empty, C.MUTED)
            return
        reach = max(3.0, self.world.player_reach(self.player))  # a bow reaches farther
        if aim is not None:
            target = self.nearest_monster(max(reach, self.pad.cfg["attackReachTiles"]))
        elif self.data.node(skill).spell is not None or skill in GROUND_BOW_SKILLS:  # aimed at the mouse, or at the monster under it
            target = self.hovered
        else:
            target = self.hovered or self.nearest_monster(reach)
        gx, gy = aim if aim is not None else self.mouse_grid()
        msg = self.world.apply(UseSkill(self.session.player_id, skill, target.id if target else None, gx, gy))
        if msg:
            self.hud.add(msg, C.MUTED)

    def skill_tooltip(self) -> list[Line] | None:
        """Lines for the skill under the mouse: a node in the tree window or a slot of the bar."""
        pos = pygame.mouse.get_pos()
        bar, keys = self.session.skill_bar, self.keys
        node_id = self.tree_view.node_at(pos)
        if node_id is not None:
            hint = f"Hover and press {keys.bar_labels}, or drag it to the bar  ·  Right-click: the right-click skill"
            return self._skill_lines(node_id, hint)
        slot = None if self.ui.over_ui or self.drag.skill else self.drag.slot_at(pos)
        if slot == RIGHT:
            if bar.right is None:
                return [Line("Right click: empty", "title"), Line("Right-click a skill in the trees, or drag one here.", "hint")]
            return self._skill_lines(bar.right, "Right click in the world uses it  ·  Right-click here: clear")
        if slot is None:
            return None
        node_id = bar.skill_at(slot)
        if node_id is None:
            return [Line(f"Key {keys.slot_label(slot)}: empty", "title"), Line("Click to choose a skill, or drag one here.", "hint")]
        return self._skill_lines(node_id, "Click: choose another  ·  Drag: move it  ·  Right-click: clear this key")

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
            if self.pad.handle(ev):
                continue  # the gamepad (menus too)
            if self.options.handle(ev) or self.credits.handle(ev) or self.select.handle(ev) or self.title.handle(ev) or self.dialogue.handle(ev):
                continue  # full-screen menus take everything
            if self.menu.handle(ev):
                continue  # paused: the menu takes everything
            if not self.dead and self.drag.handle(ev):
                continue  # skills dragged to the bar, clicks on the bar
            if self.ui.process(ev) or self.ui.typing:
                continue  # the interface (windows, text field) consumed the event
            if not self.dead and any(w.handle(ev) for w in self.windows):
                continue
            if ev.type == pygame.MOUSEBUTTONDOWN:
                self._on_click(ev.button)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                self.hold = None
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 3:
                self._release(self.session.skill_bar.right)
            elif ev.type == pygame.KEYDOWN:
                self._on_key(ev.key)
            elif ev.type == pygame.KEYUP:
                self._on_key_up(ev.key)
        if self.dead or self.paused:
            self.hold = None
            return
        self._repeat_held_click()

        # guard: hold the guard key, shield pointed at the mouse
        held = not self.ui.typing and self.keys.held("guard")
        if held or not self.pad.guarding:
            gx, gy = self.mouse_grid()
            self.world.apply(SetGuard(pid, bool(held), gx - self.player.x, gy - self.player.y))
        self.pad.update()  # the sticks: walking and the guard

    def over_waypoint_menu(self, pos) -> bool:
        if not self.waypoint_menu:
            return False
        boxes = [b for b, _ in self.waypoint_menu]
        return boxes[0].unionall(boxes).inflate(40, 80).collidepoint(pos)

    def npc_under_mouse(self) -> str | None:
        """Map name of the NPC under the cursor (its body on screen)."""
        mx, my = pygame.mouse.get_pos()
        for name, (cell, props) in self.world.layout.markers.items():
            sx, sy = self.cam.to_screen(*cell)
            if props.get("role") and pygame.Rect(sx - 14, sy - 50, 28, 54).collidepoint(mx, my):
                return name
        return None

    def _open_service(self, name: str) -> None:
        """Reaching an NPC: the merchant and the stash open their window with the backpack beside it, the crafter
        its bench, everyone else talks (npcs.json)."""
        cell, props = self.world.layout.markers[name]
        role = props.get("role")
        if role == "vendor":
            self.open_only(self.shop_view)
        elif role == "stash":
            self.open_only(self.stash_view)
        elif role == "crafter" and self.craft_view is not None:
            self.open_only(self.craft_view)
        elif props.get("npc") in self.data.npcs.npcs:
            self.dialogue.talk(props["npc"])

    def open_only(self, view) -> None:
        """One screen at a time: opening the inventory closes the trees and the other way round
        (the debug console is the exception: it stays)."""
        was_open = view.open
        self.close_windows()
        self.ui.close_picker()
        if not was_open:  # pressing its key again just closes it
            view.toggle()

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
        slot = self.drag.slot_at(pygame.mouse.get_pos())
        if slot is not None:  # the bar (left clicks and drags are SkillDrag's): right clears the key
            if button == 3:
                self.set_right_skill(None) if slot == RIGHT else self.bind_slot(slot, None)
            return
        if button == 3:  # right click in the world: the right-click skill (GDD 7.1)
            self.use_skill(self.session.skill_bar.right, self.tr("bar.noRight"))
            return
        if button != 1:
            return
        npc = self.npc_under_mouse()
        if npc is not None:  # walk there; the window opens on arrival (tick)
            self.talk_to = npc
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
                self.menu.open = True
            return
        slot = self.keys.slot_for(key)
        if slot is not None:
            node_id = self.tree_view.node_at(pygame.mouse.get_pos())
            if node_id is not None:  # D2 style: hover a skill in the trees and press the key to bind it
                self.bind_slot(slot, node_id)
            else:
                self.use_slot(slot)
            return
        self.do_action(self.keys.action_for(key))

    def do_action(self, action: str | None) -> None:
        """A keyboard or gamepad action (data/ui/keys.json names)."""
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
            self.open_only(self.tree_view)
        elif action == "inventory":
            self.open_only(self.inventory_view)
        elif action == "character":
            self.open_only(self.character_view)
        elif action == "map":
            self.big_map = not self.big_map
        elif action == "fps":
            self.show_fps = not self.show_fps
        elif action == "cancel":
            if self.ui.any_open or any(w.open for w in self.windows) or self.waypoint_menu is not None:
                self.ui.close_all()
                self.close_windows()
            else:
                self.menu.open = True  # nothing to close: the game menu (Resume, Save, Quit)

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
            return s.allocate_skill(args[0]).value
        if name == "skill":
            s.grant_skill(args[0], int(args[1]) if len(args) > 1 else 1)
            return f"{self.data.node(args[0]).name}: level {s.skills.points_in(args[0])}"
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
        if name == "stress":  # M0-06: a crowd around the hero to measure the frame rate (F3 shows it)
            n, p = int(args[0]) if args else 60, self.player
            made = 0
            for i in range(n):
                m = w.spawn_monster("bandit", max(1, s.character.level), p.x + 3 + (i % 8), p.y - 4 + (i // 8))
                made += m is not None
            self.show_fps = True
            return f"{made} monsters"
        return f"unknown command: {name}"

    # ------------------------------------------------------------ drawing

    @property
    def paused(self) -> bool:
        return self.menu.open or self.title.open or self.options.open or self.credits.open or self.select.open or self.dialogue.open

    def draw(self) -> None:
        screen, cam = self.screen, self.cam
        if self.title.open:  # the title screen: no world behind it
            screen.fill(C.BG)
            self.title.draw(screen, dim=False)
            self.select.draw(screen)
            self.options.draw(screen)
            self.credits.draw(screen)
            pygame.display.flip()
            return
        screen.fill(C.BG)
        self.map.draw(screen, cam, self.vision.explored)
        props = self.map.props(cam, self.vision.explored)
        art = self.map.art
        if art is not None:
            wp = art.waypoint(self.world.area_id in self.session.waypoints) if self.world.layout.waypoint else None
            if wp is not None and self.vision.explored[wp.y, wp.x]:
                props.append(wp)
            chests = [g for g in self.visible_drops() if isinstance(g.drop, ChestDrop)]
            props += [c for c in (art.chest(g.x, g.y) for g in chests) if c is not None]
        for prop in props:
            if prop.flat:  # stairs going down, the waypoint: part of the floor
                self.map.draw_prop(screen, cam, prop)
        self.map.draw_exit_zones(screen, cam, self.data.balance.exits.touch_radius_tiles, self.sprites.clock, self.vision.explored)
        self.map.draw_markers(screen, cam, self.fonts["tiny"], self.area_names, self.vision.explored)
        drops = self.visible_drops()
        self.sprites.draw_corpses(screen, cam, self.vision.sees)  # bodies lie under what they dropped
        shapes = drops if art is None else [g for g in drops if not isinstance(g.drop, ChestDrop)]  # chests with art are props
        self.ground_view.draw_shapes(screen, cam, shapes)
        self._draw_portal()
        self._draw_corpses()
        self.spell_view.draw_ground(screen)
        self.spell_view.draw_telegraphs(screen)
        self.spell_view.draw_slowed(screen, self.player)

        # walls, trees and actors in depth order: what is nearer the camera covers what is behind (as in D2).
        # Monsters outside the field of view are not drawn.
        actors = [(m.x + m.y, "m", m) for m in self.world.monsters.values() if self.vision.sees(m.x, m.y)]
        actors.append((self.player.x + self.player.y, "p", self.player))
        actors += [(prop.depth, "w", prop) for prop in props if not prop.flat]
        actors += [(n.x + n.y, "n", n) for n in (art.npcs if art else []) if self.vision.explored[round(n.y), round(n.x)]]
        hero = self.sprites.rects.get(("p", self.player.player_id))
        pdepth = self.player.x + self.player.y
        for _, kind, a in sorted(actors, key=lambda t: t[0]):
            if kind == "m":
                self._draw_monster(a)
            elif kind == "w":  # a wall or a tree in front of the hero turns see-through
                fade = (a.wall and hero is not None and pdepth < a.depth < pdepth + 8
                        and abs(a.x - a.y - self.player.x + self.player.y) < 6 and self.map.prop_rect(cam, a).colliderect(hero))
                self.map.draw_prop(screen, cam, a, min(a.alpha, 110) if fade else a.alpha)
            elif kind == "n":
                self._draw_npc(a)
            else:
                self._draw_player(a)

        for proj in self.world.projectiles.values():
            self._draw_projectile(proj)
        self.map.draw_exit_labels(screen, cam, self.fonts["small"], self.area_names, self.vision.explored)
        self.spell_view.draw_bursts(screen)
        self.spell_view.draw_spin(screen, self.player)
        self.spell_view.draw_barrier(screen, self.player)
        self._draw_portal_cast()
        self.ground_view.draw_labels(screen, cam, drops, self.keys.held("showItems"), self.hovered_drop, pygame.mouse.get_pos())

        for f in self.floaters:
            sx, sy = cam.to_screen(f.gx, f.gy)
            img = self.fonts["small"].render(f.text, True, f.color)
            img.set_alpha(int(255 * max(0, min(1, f.life / 0.9))))
            screen.blit(img, (sx - img.get_width() // 2, sy - 56 - (0.9 - f.life) * 45))
        if self.shake > 0:  # the world jolts; the HUD drawn next stays still
            k = int(SHAKE_PIXELS * self.shake / SHAKE_SECONDS) + 1
            t = pygame.time.get_ticks()
            screen.scroll(k if (t // 30) % 2 else -k, k if (t // 45) % 2 else -k)

        corpses = [s for s in (self.world.corpse_spot(c) for c in self.session.corpses) if s is not None]
        self.vision.draw_automap(screen, self.world.layout, self.player.x, self.player.y, big=self.big_map,
                                 portal=self.world.portal_spot(self.session.player_id), corpses=corpses)
        if self.art.status_text:
            img = self.fonts["small"].render(self.art.status_text, True, C.MUTED)
            screen.blit(img, img.get_rect(midtop=(screen.get_width() // 2, 8)))
        self.hud.draw(screen, self.world, self.player, self.hovered)
        self.ui.draw(screen)
        if self.dead:
            self._draw_death_screen()
        else:
            if self.stash_view.open or self.shop_view.open or self.craft_view.open:  # a service window shows its own backpack
                self.inventory_view.open = False
            for w in reversed(self.windows):
                w.draw(screen)
            self._draw_waypoint_menu()
            under = self.window_at(pygame.mouse.get_pos())
            tip = self.skill_tooltip() if under in (None, self.tree_view) else None
            if tip:
                draw_tooltip(screen, self.fonts, tip, pygame.mouse.get_pos())
            self.drag.draw(screen)
        self.dialogue.draw(screen)
        self.menu.draw(screen)
        self.select.draw(screen)
        self.options.draw(screen)
        self.credits.draw(screen)
        if self.show_fps:
            n = len(self.world.monsters)
            img = self.fonts["small"].render(f"{self.clock.get_fps():.0f} fps · {n} monsters", True, C.GOLD)
            screen.blit(img, img.get_rect(midtop=(screen.get_width() // 2, 28)))
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
        panel = boxes[0].unionall(boxes).inflate(40, 120)
        draw_panel(screen, panel, edge=C.WAYPOINT)
        screen.blit(f["body"].render("Waypoints", True, C.WAYPOINT), (panel.x + 20, panel.y + 10))
        for name, (cx, cy) in self.waypoint_acts:
            img = f["small"].render(name, True, C.GOLD)
            screen.blit(img, img.get_rect(center=(cx, cy)))
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

    def _draw_portal_cast(self) -> None:
        """Opening a portal: a blue light gathers at the hero's feet and grows into the gate."""
        p = self.player
        if p.portal_cast_left <= 0 or p.portal_at is None:
            return
        k = 1 - p.portal_cast_left / max(1e-6, self.world.portal_cast_seconds)
        sx, sy = self.cam.to_screen(*p.portal_at)
        t = pygame.time.get_ticks() / 1000
        for i in range(8):  # sparks spiralling up and inward where the gate will stand
            a = t * 6 + i * math.tau / 8
            rad = 46 * (1 - k) + 16
            lift = (i % 4) * 14 * k
            pygame.draw.circle(self.screen, (170, 210, 255), (sx + math.cos(a) * rad, sy - 6 - lift + math.sin(a) * rad / 2), 3)
        gate = pygame.Rect(0, 0, int(28 * k), int(52 * k))  # grows to the size of the open portal (_draw_portal)
        gate.midbottom = (sx, sy + 2)
        if gate.h > 6:
            glow = pygame.Surface(gate.size, pygame.SRCALPHA)
            pygame.draw.ellipse(glow, (60, 110, 230, 110), glow.get_rect())
            self.screen.blit(glow, gate)
            pygame.draw.ellipse(self.screen, (120, 180, 255), gate, 3)

    def _draw_corpses(self) -> None:
        """Your corpse, with your gear on it: a body on the floor and its label."""
        for c in self.session.corpses:
            spot = self.world.corpse_spot(c)
            if spot is None or not self.vision.explored[round(spot[1]), round(spot[0])]:
                continue
            sx, sy = self.cam.to_screen(*spot)
            pygame.draw.ellipse(self.screen, (0, 0, 0), (sx - 26, sy - 6, 52, 12))
            pygame.draw.rect(self.screen, (120, 110, 100), (sx - 22, sy - 12, 34, 14), border_radius=5)
            pygame.draw.circle(self.screen, (170, 150, 130), (sx + 18, sy - 6), 7)
            img = self.fonts["tiny"].render("Your corpse", True, C.GOLD)
            self.screen.blit(img, (sx - img.get_width() // 2, sy - 34))

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
        label = "Town Portal" if not self.data.is_town(self.world.area_id) else f"Portal to {self.area_names.get(portal.area_id, portal.area_id)}"
        img = self.fonts["tiny"].render(label, True, (150, 190, 255))
        screen.blit(img, (sx - img.get_width() // 2, sy - 66))

    def _draw_projectile(self, proj) -> None:
        """Flies at chest height; the line shows where it is going (it never turns)."""
        if not self.vision.sees(proj.x, proj.y):
            return
        lift = 24
        sx, sy = self.cam.to_screen(proj.x, proj.y)
        if self.effects.draw_shot(self.screen, (sx, sy), proj.look, proj.dir_x, proj.dir_y):
            return  # the art carries its own height above the ground
        tx, ty = self.cam.to_screen(proj.x - proj.dir_x * 0.8, proj.y - proj.dir_y * 0.8)
        head, tail = (sx, sy - lift), (tx, ty - lift)
        screen = self.screen
        if self.spell_view.draw_projectile(screen, proj, head, tail):
            return
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
        """Size on screen: the look scaled to its target height (sprites.json → sizes), times the rank's size."""
        lib, tid = self.sprites.lib, m.monster.type.id
        layers = self.monster_layers(m)
        return m.monster.rank.size_mult * (lib.look_scale(tid, layers) if layers is not None else lib.missing_scale(tid))

    def _draw_player(self, p) -> None:
        layers = self.player_layers(p)
        if layers is None:
            self._draw_player_shapes(p)
            return
        sx, sy = self.cam.to_screen(p.x, p.y)
        off_swing = p.state == "attacking" and p.attack_hand == "off" and p.session.gear.dual_wielding
        if off_swing:  # the left arm strikes: the figure is drawn mirrored, weapons swapped
            layers = self.sprites.lib.swap_hands(layers)
        lib = self.sprites.lib
        self.sprites.draw_actor(self.screen, self.cam, ("p", p.player_id), p, layers, scale=lib.look_scale("player", layers),
                                guarding=p.guarding, mirrored=off_swing, attack_anim=self._player_attack_anim(p))
        if p.guarding and p.state != "dead":
            self._draw_guard_arc(p, sx, sy)

    def _player_attack_anim(self, p) -> str:
        if p.attack_skill is not None and self.data.node(p.attack_skill).spell is not None:
            return "cast"
        return "shoot" if p.session.gear.has_bow else "swing"

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

    def _draw_npc(self, npc) -> None:
        """A town NPC (the merchant): its look, idle, turned toward the hero."""
        lib = self.sprites.lib
        layers = lib.layers(npc.look)
        if layers is None:
            return
        p = self.player
        dx, dy = p.x - npc.x, p.y - npc.y
        n = math.hypot(dx, dy) or 1.0
        pose = type("NpcPose", (), {"x": npc.x, "y": npc.y, "face_x": dx / n, "face_y": dy / n, "state": "idle",
                                    "attack_elapsed": 0.0, "attack_duration": 0.0})
        self.sprites.draw_actor(self.screen, self.cam, ("n", hash(npc.name) & 0xFFFF), pose, layers, scale=lib.look_scale(npc.look, layers))

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
        self.spell_view.draw_burning(screen, m, sx, sy)
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
        if self.paused:  # paused (single player): only the picture is kept up to date
            self.draw()
            return
        self.world.step(DT)  # the world keeps running while windows are open
        self._update_hover()
        if self.talk_to and self.world.marker_near(self.player, self.talk_to):
            self.world.apply(MoveTo(self.session.player_id, self.player.x, self.player.y))  # stop beside the NPC
            self._open_service(self.talk_to)
            self.talk_to = None
        for w, role in ((self.stash_view, "stash"), (self.shop_view, "vendor"), (self.craft_view, "crafter")):  # walking away closes it
            if w.open and not self.world.npc_near(self.player, role):
                w.open = False
        if self.waypoint_menu is not None and not self.world.at_waypoint(self.player):
            self.waypoint_menu = None
        self.vision.update(self.player.x, self.player.y)
        self.sprites.tick(DT)
        self.effects.tick(DT)
        note = self.art.poll()
        if note:
            self.hud.add(note, C.GOLD if self.art.state == "done" else C.LIFE)
            self._build_map()
            self.audio.reload()
            self.icons.reload()
        self.audio.watch_hero(self.player)
        self.shake = max(0.0, self.shake - DT)
        self.ui.update(DT)
        self.spell_view.update(DT)
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
        self.xp_log.close()
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
