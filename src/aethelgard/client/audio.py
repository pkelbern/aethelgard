"""Sound effects and music (M7-04, M1-05): plays the files of ``data/ui/sounds.json`` on game events.

Only listens to the core's events and the hero's state; never changes the game. Without a sound device, or
before the files are downloaded, everything is simply silent.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from ..core.events import (
    ConsumableUsed,
    DamageDealt,
    ExitReached,
    FloatingText,
    HitBlocked,
    ItemCrafted,
    LootPicked,
    MonsterKilled,
    PlayerDied,
    ProficiencyLeveledUp,
    SkillLeveledUp,
    SkillUsed,
    SpellBurst,
)
from ..core.items import GoldDrop, PotionDrop
from ..tools.fetch_sounds import sounds_dir

if TYPE_CHECKING:
    from .app import Game

MIN_GAP = 0.07  # seconds: the same effect does not restart faster than this (a crowd hit at once)


class Audio:
    def __init__(self, game: Game, cfg: dict, art_dir: Path, settings):
        self.g, self.cfg, self.dir = game, cfg, sounds_dir(art_dir)
        self.settings = settings
        self._cache: dict[str, pygame.mixer.Sound | None] = {}
        self._last: dict[str, float] = {}
        self.theme: str | None = None
        self.ok = False
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            self.ok = True
        except pygame.error:
            self.ok = False
        self._rng = random.Random(7)  # client-side variety only; the game's randomness is core.rng

    # ---- playing

    def _sound(self, name: str) -> pygame.mixer.Sound | None:
        if name not in self._cache:
            path = self.dir / f"{name}.ogg"
            try:
                self._cache[name] = pygame.mixer.Sound(str(path)) if self.ok and path.is_file() else None
            except pygame.error:
                self._cache[name] = None
        return self._cache[name]

    def play(self, effect: str, volume: float = 1.0) -> None:
        names = self.cfg["effects"].get(effect)
        if not names or not self.ok or self.settings.sfx_volume <= 0:
            return
        now = pygame.time.get_ticks() / 1000
        if now - self._last.get(effect, -1.0) < MIN_GAP:
            return
        self._last[effect] = now
        snd = self._sound(self._rng.choice(names))
        if snd is not None:
            snd.set_volume(min(1.0, volume * self.settings.sfx_volume))
            snd.play()

    def music(self, theme: str | None) -> None:
        """Plays the theme in a loop (fading from the last one); the same theme keeps playing."""
        if not self.ok or theme == self.theme:
            return
        self.theme = theme
        name = self.cfg["music"].get(theme or "")
        path = self.dir / f"{name}.ogg" if name else None
        try:
            if path is None or not path.is_file():
                pygame.mixer.music.fadeout(600)
                return
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.set_volume(self.settings.music_volume)
            pygame.mixer.music.play(-1, fade_ms=800)
        except pygame.error:
            pass

    def apply_volume(self) -> None:
        if self.ok:
            pygame.mixer.music.set_volume(self.settings.music_volume)

    def reload(self) -> None:
        """After a download: forget the silent placeholders and restart the theme."""
        self._cache.clear()
        theme, self.theme = self.theme, None
        self.music(theme)

    # ---- what plays when

    def listen(self, bus) -> None:
        pid = lambda e: getattr(e, "player_id", 0) == self.g.session.player_id  # noqa: E731
        family = lambda info: next((m.monster.type.family for m in self.g.world.monsters.values() if m.id == info.instance_id), None)  # noqa: E731
        bus.subscribe(DamageDealt, lambda e: pid(e) and self._hit(family(e.target)))
        bus.subscribe(HitBlocked, lambda e: pid(e) and self.play("block"))
        bus.subscribe(PlayerDied, lambda e: pid(e) and self.play("die"))
        bus.subscribe(MonsterKilled, lambda e: self.play(f"{self._family_of_dead(e)}Die", 0.8))
        bus.subscribe(ProficiencyLeveledUp, lambda e: pid(e) and self.play("levelUp"))
        bus.subscribe(SkillLeveledUp, lambda e: pid(e) and self.play("levelUp", 0.6))
        bus.subscribe(LootPicked, lambda e: pid(e) and e.stored and self._loot(e.drop))
        bus.subscribe(ConsumableUsed, lambda e: pid(e) and self.play("potion"))
        bus.subscribe(ItemCrafted, lambda e: pid(e) and self.play("item"))
        bus.subscribe(SpellBurst, lambda e: self.play(self.g.spell_view.look_element.get(e.look, "fire"), 0.8))
        bus.subscribe(ExitReached, lambda e: pid(e) and self.play("portal" if "portal" in e.kind else "stairs"))
        bus.subscribe(FloatingText, lambda e: e.kind == "hurt" and self.play("hurt", 0.7))
        bus.subscribe(SkillUsed, lambda e: pid(e) and self._skill(e.skill_id))

    def _hit(self, family: str | None) -> None:
        if family:
            self.play(f"{family}Hit", 0.6)

    def _family_of_dead(self, e) -> str:
        dead = next((m for m in reversed(self.g.world.fallen) if m.id == e.monster.instance_id), None)
        return dead.monster.type.family if dead else "raiders"

    def _loot(self, drop) -> None:
        self.play("coins" if isinstance(drop, GoldDrop) else "potion" if isinstance(drop, PotionDrop) else "item", 0.7)

    def _skill(self, skill_id: str) -> None:
        node = self.g.data.node(skill_id)
        if node.spell is not None:
            self.play(node.spell.element, 0.8)
        elif skill_id == "war_cry":
            self.play("warCry")

    def watch_hero(self, p) -> None:
        """Called every frame: a swing (or a shot) starting makes its sound."""
        attacking = p.state == "attacking" and p.attack_elapsed <= 1 / 60 + 1e-6
        if attacking and (p.attack_skill is None or self.g.data.node(p.attack_skill).spell is None):
            self.play("shoot" if p.session.gear.has_bow else "swing", 0.7)
